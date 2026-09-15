import json
import math
import hashlib
import http.client
import ssl
import os
from pathlib import Path
import time
import urllib.error
import urllib.parse
import urllib.request


ROOT = Path(__file__).resolve().parents[1]


class Failure(Exception):
    def __init__(self, category, message):
        super().__init__(message)
        self.category = category


def project_path(value):
    path = Path(value)
    path = (ROOT / path).resolve() if not path.is_absolute() else path.resolve()
    if not path.is_relative_to(ROOT):
        raise Failure("configuration_error", "Path must stay inside the project")
    return path


def write_json(path, value):
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def load_config(path=None):
    if path is None:
        # Per-machine settings (Vitis install root, license file) live in an
        # untracked local override. The tracked runtime.json keeps portable
        # defaults and is never edited by hand, so teammates never collide.
        local = ROOT / "serve/runtime.local.json"
        path = local if local.is_file() else "serve/runtime.json"
    config = json.loads(project_path(path).read_text(encoding="utf-8"))
    model = config["model"]
    model_keys = {"base_url", "name", "max_tokens", "temperature", "enable_thinking", "timeout_seconds", "context_tokens", "context_note", "tls_sha256"}
    hls_keys = {"vitis_root", "vivado_root", "license_file", "part", "clock_ns", "csim_timeout_seconds", "synthesis_timeout_seconds", "total_timeout_seconds"}
    if set(config) != {"model", "hls"} or set(model) != model_keys or set(config["hls"]) != hls_keys:
        raise Failure("configuration_error", "Unexpected or missing config fields; credentials must not be stored in config")
    for environment, field in [("LLM_BASE_URL", "base_url"), ("LLM_MODEL", "name")]:
        if environment in os.environ:
            model[field] = os.environ[environment]
    if "LLM_MAX_TOKENS" in os.environ:
        model["max_tokens"] = int(os.environ["LLM_MAX_TOKENS"])
    address = urllib.parse.urlsplit(model["base_url"])
    local_http = address.scheme == 'http' and address.hostname in {'localhost', '127.0.0.1', '::1'}
    if (address.scheme != 'https' and not local_http) or not address.hostname or address.username or address.password or address.query or address.fragment:
        raise Failure('configuration_error', 'API must use HTTPS or loopback HTTP without credentials/query/fragment')
    if not isinstance(model["name"], str) or not model["name"].strip():
        raise Failure("configuration_error", "Missing model name")
    if type(model["max_tokens"]) is not int or model["max_tokens"] <= 0:
        raise Failure("configuration_error", "max_tokens must be a positive integer")
    if model["max_tokens"] >= model["context_tokens"]:
        raise Failure("configuration_error", "Output budget must leave room for the prompt")
    pin = model["tls_sha256"]
    if pin and (len(pin) != 64 or any(c not in "0123456789abcdef" for c in pin)):
        raise Failure("configuration_error", "Invalid SHA256 certificate fingerprint")
    if type(model["enable_thinking"]) is not bool or not isinstance(model["temperature"], (int, float)) or not math.isfinite(model["temperature"]) or not 0 <= model["temperature"] <= 2:
        raise Failure("configuration_error", "Invalid generation parameters")
    for value in [model["timeout_seconds"], config["hls"]["clock_ns"], config["hls"]["csim_timeout_seconds"], config["hls"]["synthesis_timeout_seconds"], config["hls"]["total_timeout_seconds"]]:
        if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
            raise Failure("configuration_error", "Timeouts and clock period must be positive finite numbers")
    return config


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        return None


def generate(problem, config, output, timeout=None):
    if not problem.strip():
        raise Failure("input_error", "Problem is empty")
    if output.exists() or Path(str(output) + ".meta.json").exists():
        raise Failure("input_error", "Refusing to overwrite a previous response")
    key = os.environ.get("LLM_API_KEY", "not-needed").strip()
    model = config["model"]
    payload = {
        "model": model["name"],
        "messages": [{"role": "user", "content": problem}],
        "max_tokens": model["max_tokens"],
        "temperature": model["temperature"],
        "chat_template_kwargs": {"enable_thinking": model["enable_thinking"]},
        "stream": False,
    }
    address = urllib.parse.urlsplit(model["base_url"])
    # Pin only this connection. Never change global TLS defaults or follow redirects.
    context = ssl._create_unverified_context() if model["tls_sha256"] else ssl.create_default_context()
    if address.scheme == 'http':
        if address.hostname not in {'localhost', '127.0.0.1', '::1'} or model['tls_sha256']:
            raise Failure('configuration_error', 'HTTP requires loopback without a TLS pin')
        connection = http.client.HTTPConnection(address.hostname, address.port or 80, timeout=timeout or model['timeout_seconds'])
    else:
        connection = http.client.HTTPSConnection(address.hostname, address.port or 443, context=context, timeout=timeout or model['timeout_seconds'])
    started = time.monotonic()
    metadata = {"model": model, "requests": 0, "development_external_api": address.hostname not in {"localhost", "127.0.0.1", "::1"}}
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        connection.connect()
        if model["tls_sha256"] and hashlib.sha256(connection.sock.getpeercert(binary_form=True)).hexdigest() != model["tls_sha256"]:
            raise Failure("api_tls_error", "Server certificate changed; verify and update tls_sha256 explicitly")
        metadata["requests"] = 1
        connection.request("POST", address.path.rstrip("/") + "/chat/completions", body=json.dumps(payload).encode("utf-8"), headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"})
        response = connection.getresponse()
        if response.status != 200:
            metadata["http_status"] = response.status
            category = {400: "api_request_or_context_error", 401: "api_auth_error", 403: "api_auth_error", 429: "api_quota_or_rate_limit"}.get(response.status, "api_http_error")
            raise Failure(category, "API returned HTTP " + str(response.status) + "; no retry")
        result = json.load(response)
        choice = result["choices"][0]
        content = choice["message"]["content"]
        if not isinstance(content, str) or not content.strip():
            raise Failure("api_response_error", "Response has no usable text")
        output.write_text(content, encoding="utf-8")
        metadata.update(usage=result.get("usage"), finish_reason=choice.get("finish_reason"))
        if choice.get("finish_reason") != "stop":
            raise Failure("generation_incomplete", "Raw response saved, but generation did not finish normally")
        metadata["status"] = "passed"
    except urllib.error.HTTPError as error:
        category = {401: "api_auth_error", 403: "api_auth_error", 429: "api_quota_or_rate_limit"}.get(error.code, "api_http_error")
        metadata.update(status="failed", category=category, http_status=error.code)
        raise Failure(category, "API returned HTTP " + str(error.code) + "; no retry") from None
    except (OSError, http.client.HTTPException):
        metadata.update(status="failed", category="api_network_or_timeout")
        raise Failure("api_network_or_timeout", "API network failure or timeout; no retry") from None
    except (ValueError, KeyError, IndexError, TypeError):
        metadata.update(status="failed", category="api_response_error")
        raise Failure("api_response_error", "Malformed API response; no retry") from None
    except Failure as error:
        metadata.update(status="failed", category=error.category)
        raise
    finally:
        connection.close()
        metadata["elapsed_seconds"] = round(time.monotonic() - started, 3)
        write_json(Path(str(output) + ".meta.json"), metadata)
    return metadata
