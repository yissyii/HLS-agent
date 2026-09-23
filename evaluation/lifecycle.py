"""Single bridge to the removable development lifecycle.

All evaluation CLIs enter through evaluate(). Low-level single-attempt functions
remain usable by the lifecycle and contract tests. Removing local_eval/ makes
these hooks pass through; errors *inside* an installed module never disable it.
"""
from importlib import import_module


def _development():
    try:
        return import_module('local_eval.guard')
    except ModuleNotFoundError as error:
        if error.name == 'local_eval':
            return None
        raise


def evaluate(args, run_once, *, output=None, output_is_file=False):
    module = _development()
    if module is None:
        return run_once(args)
    return module.evaluate(args, run_once, output=output, output_is_file=output_is_file)


def output_path(path):
    module = _development()
    return module.output_path(path) if module else path


def before_request():
    module = _development()
    if module:
        module.before_request()


def request_retry_settings():
    module = _development()
    return module.request_retry_settings() if module else None


def observe_request(path, metadata):
    module = _development()
    if module:
        module.observe_request(path, metadata)
