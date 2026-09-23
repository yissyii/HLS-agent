"""Retry external requests in place; never restart completed evaluations."""
import json
import math
from pathlib import Path
import time

from serve.inference import write_json


def load_settings(path=None, max_restarts=None):
    settings = json.loads(Path(path or Path(__file__).with_name('retry.json')).read_text(encoding='utf-8'))
    if max_restarts is not None:
        settings['max_restarts'] = max_restarts
    if set(settings) != {'max_restarts', 'initial_delay_seconds', 'max_delay_seconds',
                         'retry_http_statuses', 'retry_generation_timeout'}:
        raise ValueError('Unexpected or missing local retry setting')
    if type(settings['max_restarts']) is not int or not 0 <= settings['max_restarts'] <= 20:
        raise ValueError('max_restarts must be an integer in 0..20')
    for key in ('initial_delay_seconds', 'max_delay_seconds'):
        value = settings[key]
        if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 300:
            raise ValueError(key + ' must be finite and in 0..300')
    if settings['max_delay_seconds'] < settings['initial_delay_seconds']:
        raise ValueError('max_delay_seconds must be >= initial_delay_seconds')
    statuses = settings['retry_http_statuses']
    if (not isinstance(statuses, list) or any(type(v) is not int or v not in
            {408, 429, 500, 502, 503, 504} for v in statuses)):
        raise ValueError('Only transient HTTP statuses 408/429/500/502/503/504 may be retried')
    if type(settings['retry_generation_timeout']) is not bool:
        raise ValueError('retry_generation_timeout must be boolean')
    return settings


def classify(metadata, settings):
    """Only structured model-transport evidence, never generated text/tool logs."""
    category = metadata.get('category')
    status = metadata.get('http_status')
    if category in {'api_network_or_timeout', 'api_auth_error', 'api_tls_error', 'api_response_error'}:
        return category
    if category in {'api_http_error', 'api_quota_or_rate_limit'}:
        return 'http_' + str(status)
    # The worker deadline may be exhausted by model speed/total task budget,
    # rather than a network fault. Do not discard it by default.
    if category == 'generation_timeout' and settings['retry_generation_timeout']:
        return category
    return None


def run_session(output, settings, run_once, *, sleep=time.sleep, verify=lambda: None):
    """run_once must finish/clean up its children before returning its receipt."""
    output = Path(output)
    summary = dict(schema_version=1, local_only=True, status='running',
                   retry=settings, valid_attempt=None, attempts=[])
    started = time.monotonic()

    def save():
        summary['elapsed_seconds'] = round(time.monotonic() - started, 3)
        summary['api_requests_recorded_all_attempts'] = sum(a.get('api_requests_recorded', 0) for a in summary['attempts'])
        summary['request_outcomes_unknown_all_attempts'] = sum(a.get('request_outcomes_unknown', 0) for a in summary['attempts'])
        write_json(output / 'session.json', summary)

    save()
    try:
        for number in range(1):  # Never restart the dataset.
            verify()
            attempt = output / ('attempt_%03d' % number)
            attempt.mkdir(exist_ok=False)
            record = dict(attempt=attempt.name, status='running', valid_for_metrics=False)
            summary['attempts'].append(record)
            save()
            result = run_once(attempt)
            verify()
            record.update(result)
            if not result['network_failures']:
                record.update(status='accepted', valid_for_metrics=True)
                summary.update(status='completed' if result['exit_code'] == 0 else 'completed_with_failures',
                               valid_attempt=attempt.name)
                save()
                return result['exit_code'], summary
            record.update(status='retained_with_network_failures', valid_for_metrics=False)
            summary.update(status='completed_with_network_failures', valid_attempt=attempt.name)
            save()
            return 2, summary
    except BaseException as error:
        summary.update(status='interrupted' if isinstance(error, KeyboardInterrupt) else 'runner_error',
                       error=str(error), valid_attempt=None)
        if summary['attempts'] and summary['attempts'][-1]['status'] == 'running':
            summary['attempts'][-1]['status'] = summary['status']
        save()
        raise
