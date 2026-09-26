"""Remote Linux worker for isolated native C++ validation jobs."""
from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from typing import Any


COMPILE_TIMEOUT = 120
RUN_TIMEOUT = 10


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _safe_file(root: Path, value: Any, label: str) -> Path:
    if not isinstance(value, str) or not value or Path(value).is_absolute():
        raise ValueError("invalid " + label + " path")
    candidate = (root / value).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        raise ValueError(label + " path escapes jobs directory") from None
    if not candidate.is_file():
        raise ValueError(label + " must name a regular file")
    return candidate


def _job_id(value: Any) -> str:
    if not isinstance(value, str) or not value or any(ch not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for ch in value):
        raise ValueError("invalid job id")
    return value


def _expected_hash(value: Any, label: str) -> str:
    if not isinstance(value, str) or len(value) != 64 or any(ch not in "0123456789abcdef" for ch in value.lower()):
        raise ValueError("invalid " + label + " sha256")
    return value.lower()


def _atomic_json(path: Path, value: Any) -> None:
    descriptor, temporary = tempfile.mkstemp(prefix=".results-", suffix=".json", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(value, stream, sort_keys=True, indent=2)
            stream.write("\n")
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def _write_log(path: Path, content: bytes | None) -> None:
    path.write_bytes(content or b"")


def _invoke(argv: list[str], timeout: int, stdout_log: Path, stderr_log: Path) -> dict[str, Any]:
    started = time.monotonic()
    try:
        completed = subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, timeout=timeout, check=False)
        stdout, stderr = completed.stdout, completed.stderr
        result = {"returncode": completed.returncode, "timeout": False}
    except subprocess.TimeoutExpired as error:
        stdout, stderr = error.stdout, error.stderr
        result = {"returncode": None, "timeout": True}
    except OSError as error:
        stdout, stderr = b"", str(error).encode("utf-8", "replace")
        result = {"returncode": None, "timeout": False, "error": str(error)}
    _write_log(stdout_log, stdout)
    _write_log(stderr_log, stderr)
    result.update({"elapsed_seconds": time.monotonic() - started,
                   "stdout_log": stdout_log.name, "stderr_log": stderr_log.name})
    return result


def process_job(root: Path, include: Path, build: Path, raw: Any) -> dict[str, Any]:
    """Process one untrusted job description without invoking a shell."""
    started = time.monotonic()
    result: dict[str, Any] = {"status": "rejected", "elapsed_seconds": 0.0}
    try:
        if not isinstance(raw, dict):
            raise ValueError("job must be an object")
        identifier = _job_id(raw.get("id"))
        result["id"] = identifier
        source = _safe_file(root, raw.get("source"), "source")
        testbench = _safe_file(root, raw.get("testbench"), "testbench")
        source_expected = _expected_hash(raw.get("source_sha256"), "source")
        testbench_expected = _expected_hash(raw.get("testbench_sha256"), "testbench")
        source_actual, testbench_actual = _sha256(source), _sha256(testbench)
        result["hashes"] = {"source": source_actual, "testbench": testbench_actual}
        if source_actual != source_expected or testbench_actual != testbench_expected:
            result.update({"status": "hash_rejected", "hash_verified": False})
            return result
        result["hash_verified"] = True
        compile_stdout = build / (identifier + ".compile.stdout.log")
        compile_stderr = build / (identifier + ".compile.stderr.log")
        compile_result = _invoke(["g++", "-std=c++17", "-O0", "-I", str(include), str(source),
                                  str(testbench), "-o", str(build / identifier)], COMPILE_TIMEOUT,
                                 compile_stdout, compile_stderr)
        result["compile"] = compile_result
        if compile_result["timeout"] or compile_result["returncode"] != 0:
            result["status"] = "compile_failed"
            return result
        run_stdout = build / (identifier + ".run.stdout.log")
        run_stderr = build / (identifier + ".run.stderr.log")
        run_result = _invoke([str(build / identifier)], RUN_TIMEOUT, run_stdout, run_stderr)
        result["run"] = run_result
        result["status"] = "passed" if not run_result["timeout"] and run_result["returncode"] == 0 else "failed"
        return result
    except ValueError as error:
        result["error"] = str(error)
        return result
    finally:
        result["elapsed_seconds"] = time.monotonic() - started


def run_jobs(jobs_path: str | Path, include: str | Path, workers: int = 2) -> dict[str, Any]:
    jobs_file = Path(jobs_path).resolve()
    if not jobs_file.is_file():
        raise ValueError("jobs must name a regular file")
    include_path = Path(include).resolve()
    if not include_path.is_dir():
        raise ValueError("include must name a directory")
    if workers not in {1, 2}:
        raise ValueError("workers must be 1 or 2")
    with jobs_file.open("r", encoding="utf-8") as stream:
        document = json.load(stream)
    if not isinstance(document, dict) or set(document) != {"jobs"} or not isinstance(document["jobs"], list):
        raise ValueError("jobs JSON must be an object containing only jobs")
    root = jobs_file.parent.resolve()
    build = root / "build"
    build.mkdir(mode=0o700, exist_ok=True)
    results_path = root / "results.json"
    records: list[dict[str, Any] | None] = [None] * len(document["jobs"])
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        pending = {executor.submit(process_job, root, include_path, build, raw): index
                   for index, raw in enumerate(document["jobs"])}
        for future in concurrent.futures.as_completed(pending):
            index = pending[future]
            try:
                records[index] = future.result()
            except BaseException as error:
                records[index] = {"status": "worker_error", "error": str(error), "elapsed_seconds": 0.0}
            current = {"jobs": [record for record in records if record is not None]}
            _atomic_json(results_path, current)
            label = records[index].get("id", "invalid")
            print(label + ": " + records[index]["status"], flush=True)
    final = {"jobs": [record for record in records if record is not None]}
    _atomic_json(results_path, final)
    return final


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--jobs", required=True)
    parser.add_argument("--include", required=True)
    parser.add_argument("--workers", type=int, choices=(1, 2), default=2)
    options = parser.parse_args(argv)
    try:
        run_jobs(options.jobs, options.include, options.workers)
    except (ValueError, json.JSONDecodeError) as error:
        print("error: " + str(error), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
