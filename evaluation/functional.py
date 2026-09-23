"""Observed simulation facts, separate from the permission to release them."""
import json
import re
import math


_FIELDS = {'cycle', 'index', 'signal', 'inputs', 'expected', 'actual', 'reset', 'history'}
_KINDS = {'output_mismatch', 'assertion_failure', 'runtime_exception', 'deadlock', 'test_failure', 'nonzero_exit'}
_RUNTIME = re.compile(r'(?:^|:\s)(?:runtime error:\s|AddressSanitizer:|UndefinedBehaviorSanitizer:)', re.I)
HEADER = 'Functional diagnostic (observed facts; absent fields were not reported):\n'

# Explicit testbench formats only; arbitrary expected/got text is not evidence.
_TEST_ERROR = re.compile(
    r'^(?:(?:Fixed|Random) test error at cycle (?P<cycle>\d+)|'
    r'Error at test case (?P<index>\d+))(?=\s*:|\s+for input\b)', re.I)
_VALUE = r'[+-]?(?:0x[0-9a-f]+|0b[01]+|(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?|true|false)'
_TEST_PAIR = re.compile(
    r'\bexpected\s+(?:(?P<expected_signal>[A-Za-z_]\w*)\s*=\s*|[:=]\s*)?'
    r'(?P<expected>' + _VALUE + r')\s*,\s*(?:got|actual)\s+'
    r'(?:(?P<actual_signal>[A-Za-z_]\w*)\s*=\s*|[:=]\s*)?'
    r'(?P<actual>' + _VALUE + r')\s*$', re.I)


def _bounded(value, depth=0):
    if depth > 3:
        return '<omitted>', True
    if isinstance(value, str):
        return value[:200], len(value) > 200
    if type(value) is float and not math.isfinite(value):
        return '<nonfinite>', True
    if value is None or type(value) in (int, float, bool):
        return value, False
    if isinstance(value, (list, dict)):
        pairs = list(enumerate(value)) if isinstance(value, list) else list(value.items())
        result = [] if isinstance(value, list) else {}
        trimmed = len(pairs) > 8
        for key, item in pairs[:8]:
            item, cut = _bounded(item, depth + 1)
            trimmed |= cut
            if isinstance(result, list):
                result.append(item)
            else:
                result[str(key)[:80]] = item
                trimmed |= len(str(key)) > 80
        return result, trimmed
    return '<unsupported>', True


def parse_line(line):
    """Return only explicitly logged fields. Missing inputs/history are not inferred."""
    line = line.strip()
    if line.startswith('ZCOMP_FUNCTIONAL '):
        try:
            data = json.loads(line[len('ZCOMP_FUNCTIONAL '):],
                              parse_constant=lambda _: (_ for _ in ()).throw(ValueError('nonfinite')))
        except (ValueError, RecursionError):
            return None
        if not isinstance(data, dict) or data.get('kind') not in _KINDS:
            return None
        fields, trimmed = _bounded({k: v for k, v in data.items() if k in _FIELDS})
        return dict(failure_kind=data['kind'], observed=fields, truncated=trimmed, format='zcomp_json_v1')
    test_error = _TEST_ERROR.match(line)
    if test_error:
        pair = _TEST_PAIR.search(line, test_error.end())
        if not pair or pair['expected_signal'] != pair['actual_signal']:
            return None
        fields = {name: int(value) for name, value in test_error.groupdict().items() if value is not None}
        fields.update(expected=pair['expected'], actual=pair['actual'])
        if pair['expected_signal']:
            fields['signal'] = pair['expected_signal']
        # Free-form input prose is deliberately not interpreted as input/history.
        fields, trimmed = _bounded(fields)
        return dict(failure_kind='output_mismatch', observed=fields, truncated=trimmed, format='text')
    if re.match(r'^Mismatch\b', line, re.I):
        fields = {}
        pair = re.search(r'expected\s*[:=]?\s*(.+?)\s*,?\s+(?:got|actual)\s*[:=]?\s*(.+)$', line, re.I)
        if pair:
            fields.update(expected=pair.group(1).rstrip(', '), actual=pair.group(2).strip())
        for name, pattern in [('cycle', r'\bcycle\s*[=:]?\s*(\d+)'),
                              ('index', r'\b(?:index|sample)\s*[=:]?\s*(\d+)'),
                              ('signal', r'\b(?:signal|output)\s*[=:]?\s*([A-Za-z_]\w*)')]:
            match = re.search(pattern, line, re.I)
            if match:
                fields[name] = int(match.group(1)) if name != 'signal' else match.group(1)
        fields, trimmed = _bounded(fields)
        return dict(failure_kind='output_mismatch', observed=fields, truncated=trimmed, format='text')
    if re.match(r'^Test Failed\b', line, re.I):
        fields = {}
        counts = re.search(r'(\d+) mismatches.*?out of (\d+) cases', line, re.I)
        if counts:
            fields = dict(mismatches_reported=int(counts.group(1)), cases_reported=int(counts.group(2)))
        return dict(failure_kind='test_failure', observed=fields, truncated=False, format='text')
    nonzero = re.search(r"^@E Simulation failed:.*returns nonzero value ['\"](-?\d+)['\"]", line, re.I)
    if nonzero:
        return dict(failure_kind='nonzero_exit', observed={'program_exit_code': int(nonzero.group(1))},
                    truncated=False, format='text')
    runtime = None
    if re.search(r'\bAssertion\s+.+\s+failed[.!]?$', line, re.I):
        runtime = 'assertion_failure'
    elif re.match(r'^(?:Segmentation fault|Floating point exception|terminate called after throwing)\b', line, re.I) or _RUNTIME.search(line):
        runtime = 'runtime_exception'
    elif re.match(r'^(?:ERROR:\s*)?deadlock detected\b', line, re.I):
        runtime = 'deadlock'
    if runtime:
        message, trimmed = _bounded(line)
        return dict(failure_kind=runtime, observed={'message': message}, truncated=trimmed, format='text')
    return None


def extract_functional(stage, execution, text):
    events, seen, observed_count = [], set(), 0
    if stage == 'csim':
        for number, line in enumerate(text.splitlines(), 1):
            event = parse_line(line)
            if event is None:
                continue
            observed_count += 1
            key = json.dumps(event, sort_keys=True)
            if key not in seen:
                seen.add(key)
                if len(events) < 20:
                    events.append(dict(event, log_line=number))
    return dict(schema_version=1, source='csim_log', events=events,
                observed_events=observed_count, unique_events=len(seen),
                omitted_unique_events=max(0, len(seen)-len(events)),
                tool_exit_code=execution.get('exit_code'), timed_out=bool(execution.get('timed_out')))


def format_functional(report, max_chars=2800):
    if not report or not report.get('events'):
        return ''
    # Prefer concrete events over generic test/exit summaries; stable log order.
    events = sorted(report['events'], key=lambda e: e['failure_kind'] in {'test_failure', 'nonzero_exit'})
    selected = []
    def render():
        return HEADER + json.dumps(
            dict(schema_version=1, source=report['source'], events=selected,
                 observed_events=report['observed_events'],
                 omitted_unique_events=report['unique_events']-len(selected)), ensure_ascii=False)
    for event in events:
        if len(selected) == 3:
            break
        selected.append(event)
        if len(render()) > max_chars:
            selected.pop()
    return render() if selected else ''


def fit_feedback(text, max_chars):
    """Trim structured feedback by whole events, never cut JSON/expected-actual pairs."""
    if len(text) <= max_chars:
        return text
    if text.startswith(HEADER):
        try:
            payload = json.loads(text[len(HEADER):])
            payload['unique_events'] = len(payload['events']) + payload['omitted_unique_events']
            return format_functional(payload, max_chars) or 'functional_or_runtime_error. Structured evidence omitted by context budget.'
        except (ValueError, KeyError, TypeError):
            return 'functional_or_runtime_error. Structured evidence unavailable.'
    return text[:max_chars]
