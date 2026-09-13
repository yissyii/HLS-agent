import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import sys
import time
import uuid

from evaluation.hls import environment, run_process, validate_stage
from serve.inference import Failure, ROOT, load_config, project_path, write_json


def relative_name(value):
    if not isinstance(value, str) or not value or "\\" in value or any(ord(character) < 32 for character in value):
        raise Failure("input_error", "Invalid manifest path")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or ".secrets" in path.parts or ":" in value:
        raise Failure("input_error", "Manifest file paths must be safe relative paths")
    return str(path)


def load_manifest(path):
    manifest = json.loads(path.read_text(encoding="utf-8"))
    for field in ["id", "top_function"]:
        pattern = r"[A-Za-z0-9_][A-Za-z0-9_-]*" if field == "id" else r"[A-Za-z_][A-Za-z0-9_]*"
        if not isinstance(manifest[field], str) or not re.fullmatch(pattern, manifest[field]):
            raise Failure("input_error", field + " must be a simple identifier")
    manifest["source_file"] = relative_name(manifest.get("source_file", "kernel.cpp"))
    if Path(manifest["source_file"]).suffix not in [".cpp", ".cc", ".cxx", ".c"]:
        raise Failure("input_error", "Generated source must be a C/C++ file")
    manifest["problem_file"] = relative_name(manifest["problem_file"])
    for field in ["testbench_files", "design_files", "support_files", "include_dirs"]:
        default = ["."] if field == "include_dirs" else []
        values = manifest.get(field, default)
        if not isinstance(values, list):
            raise Failure("input_error", field + " must be a list")
        manifest[field] = [relative_name(value) for value in values]
    if not manifest["testbench_files"]:
        raise Failure("input_error", "At least one testbench file is required")
    manifest["cxx_standard"] = manifest.get("cxx_standard", "c++14")
    if manifest["cxx_standard"] not in ["c++11", "c++14", "c++17"]:
        raise Failure("input_error", "Unsupported C++ standard")
    names = [manifest["problem_file"]] + manifest["testbench_files"] + manifest["design_files"] + manifest["support_files"]
    if len(set(names)) != len(names) or manifest["source_file"] in names:
        raise Failure("input_error", "Manifest files must not collide")
    for name in names:
        target = (path.parent / name).resolve()
        if not target.is_relative_to(path.parent) or not target.is_file():
            raise Failure("input_error", "Manifest dependency missing or outside task directory: " + name)
    return manifest, names


def extract_code(text):
    stripped = text.strip()
    if "```" not in stripped:
        return text, "verbatim"
    match = re.fullmatch(r"```(?:cpp|c\+\+|c|cc)?\s*\n(.*?)\n```", stripped, re.DOTALL | re.IGNORECASE)
    if not match or "```" in match.group(1):
        raise Failure("response_format_error", "Ambiguous Markdown response; no guessing or regeneration")
    code = match.group(1) + "\n"
    if not code.strip():
        raise Failure("response_format_error", "Empty code block")
    return code, "single_outer_fence_removed"


def repair_prompt(problem, source, diagnostic, manifest):
    return ("Repair this Vitis HLS C++ kernel. Return only the complete replacement source in one optional cpp fence; no explanation. Preserve the required top function and behavior. Fix the tool error, especially typedef pointer dimensions, array indexing, and exact header declarations.\n\n<PROBLEM>\n" + problem + "\n</PROBLEM>\n<TOP_FUNCTION>" + manifest["top_function"] + "</TOP_FUNCTION>\n<CURRENT_SOURCE>\n```cpp\n" + source + "\n```\n</CURRENT_SOURCE>\n<TOOL_DIAGNOSTIC>\n" + diagnostic[-12000:] + "\n</TOOL_DIAGNOSTIC>\n")


def evaluate(args):
    started = time.monotonic()
    output_root = project_path("output/runs")
    output_root.mkdir(parents=True, exist_ok=True)
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + "-" + uuid.uuid4().hex[:8]
    work = output_root / run_id
    work.mkdir(mode=0o700)
    summary = {
        "schema_version": 1,
        "run_id": run_id,
        "status": "running",
        "mode": ("repair_existing" if args.source else "repair_and_validate") if args.repair_attempts else ("validate_existing" if args.source else "baseline_and_validate"),
        "repair_attempts_allowed": args.repair_attempts,
        "repair_attempts_used": 0,
        "cpu_only_requested": args.cpu_only,
        "stages": {name: {"status": "not_run"} for name in ["generation", "csim", "synthesis"]},
        "checks": {"parse": "not_run", "compile": "not_run", "run": "not_run", "synthesize": "not_run"},
        "check_note": "C parsing and compilation are observed together through Vitis csim, not independent official scoring stages. Testbench must return nonzero on failure.",
    }
    result_path = work / "result.json"
    write_json(result_path, summary)
    active = None
    try:
        config = load_config(args.config)
        write_json(work / "config.json", config)
        manifest_path = project_path(args.manifest)
        manifest, names = load_manifest(manifest_path)
        write_json(work / "manifest.json", manifest)
        summary["task_id"] = manifest["id"]
        inputs = work / "input"
        inputs.mkdir()
        for name in names:
            target = inputs / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(manifest_path.parent / name, target)
        summary["input_sha256"] = {name: hashlib.sha256((inputs / name).read_bytes()).hexdigest() for name in names}
        for directory in manifest["include_dirs"]:
            if not (inputs / directory).is_dir():
                raise Failure("input_error", "Include directory missing from copied inputs: " + directory)
        source = inputs / manifest["source_file"]
        source.parent.mkdir(parents=True, exist_ok=True)
        environment(work, config["hls"], args.cpu_only)
        deadline = started + config["hls"]["total_timeout_seconds"]
        if args.source:
            original = project_path(args.source)
            if ".secrets" in original.parts or original.suffix not in [".cpp", ".cc", ".cxx", ".c"]:
                raise Failure("input_error", "Expected a C/C++ source file")
            shutil.copyfile(original, source)
            summary["stages"]["generation"] = {"status": "skipped", "requests": 0}
            summary["source_origin"] = str(original.relative_to(ROOT))
        else:
            active = "generation"
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise Failure("total_timeout", "Total budget exhausted before generation")
            raw = work / "response.txt"
            values = os.environ.copy()
            values["PYTHONDONTWRITEBYTECODE"] = "1"
            if args.cpu_only:
                for name in ["CUDA_VISIBLE_DEVICES", "HIP_VISIBLE_DEVICES", "ROCR_VISIBLE_DEVICES", "GPU_DEVICE_ORDINAL"]:
                    values[name] = "-1"
            execution = run_process(
                [sys.executable, str(ROOT / "serve/baseline.py"), str(inputs / manifest["problem_file"]), str(raw), "--config", str(work / "config.json")],
                work, values, work / "generation.log", min(remaining, config["model"]["timeout_seconds"]),
            )
            metadata_path = Path(str(raw) + ".meta.json")
            metadata = json.loads(metadata_path.read_text(encoding="utf-8")) if metadata_path.is_file() else {}
            summary["stages"][active] = {**metadata, **execution, "status": "passed" if execution["exit_code"] == 0 and not execution["timed_out"] else "failed"}
            if summary["stages"][active]["status"] != "passed":
                category = "generation_timeout" if execution["timed_out"] else metadata.get("category", "generation_error")
                summary["stages"][active]["category"] = category
                raise Failure(category, "Generation failed; inspect generation.log; no retry")
            code, transformation = extract_code(raw.read_text(encoding="utf-8"))
            source.write_text(code, encoding="utf-8")
            summary["source_extraction"] = transformation
        summary["source_sha256"] = hashlib.sha256(source.read_bytes()).hexdigest()
        write_json(result_path, summary)
        shutil.copyfile(source, work / "candidate_00.cpp")
        active = "csim"
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise Failure("total_timeout", "Total budget exhausted before " + active)
            outcome = validate_stage(active, work, manifest, config["hls"], args.cpu_only, remaining)
            archive = work / ("attempt_%02d" % summary["repair_attempts_used"])
            archive.mkdir(exist_ok=True)
            shutil.copyfile(work / outcome["log"], archive / outcome["log"])
            outcome["log"] = str((archive / outcome["log"]).relative_to(work))
            summary.setdefault("validation_history", []).append({"attempt": summary["repair_attempts_used"], "stage": active, "outcome": outcome})
            summary["stages"][active] = outcome
            if active == "csim":
                if outcome["status"] == "passed":
                    summary["checks"].update(parse="passed", compile="passed", run="passed")
                elif outcome.get("category") == "functional_or_runtime_error":
                    summary["checks"].update(parse="passed", compile="passed", run="failed")
                elif outcome.get("category") == "compile_error":
                    summary["checks"].update(parse="unknown", compile="failed", run="not_run")
                else:
                    summary["checks"].update(parse="unknown", compile="unknown", run="not_run")
            else:
                summary["checks"]["synthesize"] = outcome["status"]
            write_json(result_path, summary)
            if outcome["status"] != "passed":
                if outcome.get("category") not in {"compile_error", "functional_or_runtime_error", "synthesis_error", "compile_or_csim_error"}:
                    raise Failure(outcome["category"], "Infrastructure failure: no model repair")
                if not args.repair_attempts or summary["repair_attempts_used"] >= args.repair_attempts:
                    raise Failure(outcome["category"], active + " failed; repair budget exhausted; inspect " + outcome["log"])
                summary["repair_attempts_used"] += 1
                attempt = summary["repair_attempts_used"]
                raw = work / ("repair_%02d_response.txt" % attempt)
                prompt = repair_prompt((inputs / manifest["problem_file"]).read_text(encoding="utf-8"), source.read_text(encoding="utf-8"), outcome.get("diagnostic_tail", ""), manifest)
                for name in manifest["support_files"]:
                    if Path(name).suffix in {".h", ".hpp", ".hh"}:
                        prompt += "\n<HEADER name=" + name + ">\n" + (inputs / name).read_text(encoding="utf-8") + "\n</HEADER>\n"
                prompt_path = work / ("repair_%02d_prompt.txt" % attempt)
                if len(prompt) > 60000:
                    raise Failure("repair_context_limit", "Repair prompt exceeds 60000 characters; refusing silent truncation")
                prompt_path.write_text(prompt, encoding="utf-8")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise Failure("total_timeout", "No budget for repair")
                repair_execution = run_process([sys.executable, str(ROOT / "serve/baseline.py"), str(prompt_path), str(raw), "--config", str(work / "config.json")], work, dict(os.environ, PYTHONDONTWRITEBYTECODE="1"), work / ("repair_%02d.log" % attempt), min(remaining, config["model"]["timeout_seconds"]))
                repair = json.loads(Path(str(raw) + ".meta.json").read_text()) if Path(str(raw) + ".meta.json").is_file() else {}
                summary.setdefault("repair_requests", []).append({"attempt": attempt, "kind": "repair", "execution": repair_execution, "metadata": repair})
                if repair_execution["timed_out"] or repair_execution["exit_code"] != 0:
                    raise Failure("repair_request_failed", "Repair request failed or timed out; no retry")
                code, transformation = extract_code(raw.read_text(encoding="utf-8"))
                candidate = work / ("candidate_%02d.cpp" % attempt)
                candidate.write_text(code, encoding="utf-8")
                digest = hashlib.sha256(candidate.read_bytes()).hexdigest()
                if digest in summary.setdefault("seen_source_hashes", [summary["source_sha256"]]):
                    raise Failure("repeated_candidate", "Model returned an already validated candidate")
                summary["seen_source_hashes"].append(digest)
                shutil.copyfile(candidate, source)
                summary["source_sha256"] = digest
                summary["checks"] = {name: "not_run" for name in ["parse", "compile", "run", "synthesize"]}
                summary["stages"]["csim"] = {"status": "not_run"}
                summary["stages"]["synthesis"] = {"status": "not_run"}
                summary.setdefault("repairs", []).append({"attempt": attempt, "after_stage": active, "response": raw.name, "candidate": candidate.name, "source_extraction": transformation, "request": repair})
                write_json(result_path, summary)
                active = "csim"
                continue
            if active == "synthesis":
                break
            active = "synthesis"
        summary["status"] = "passed"
    except Failure as error:
        summary.update(status="failed", category=error.category, message=str(error))
        if active and summary["stages"][active]["status"] == "not_run":
            summary["stages"][active] = {"status": "failed", "category": error.category}
    except (OSError, ValueError, KeyError, TypeError):
        summary.update(status="failed", category="configuration_or_io_error", message="Check manifest, configuration, and file permissions")
    except KeyboardInterrupt:
        summary.update(status="interrupted", category="interrupted", message="Interrupted by user")
    except Exception as error:
        summary.update(status="failed", category="internal_error", message="Unexpected evaluator error: " + type(error).__name__)
    finally:
        summary["api_requests_recorded"] = summary["stages"]["generation"].get("requests", 0) + sum(record["metadata"].get("requests", 0) for record in summary.get("repair_requests", []))
        summary["request_count_note"] = "Recorded requests only; a timed-out process without metadata may have sent a request."
        candidates = {}
        for record in summary.get("validation_history", []):
            rank = 2 if record["stage"] == "synthesis" and record["outcome"]["status"] == "passed" else 1 if record["stage"] == "csim" and record["outcome"]["status"] == "passed" else 0
            candidates[record["attempt"]] = max(candidates.get(record["attempt"], 0), rank)
        if candidates:
            best = max(candidates, key=lambda number: (candidates[number], -number))
            summary["best_candidate"] = "candidate_%02d.cpp" % best
            summary["best_validation_level"] = candidates[best]
        summary["elapsed_seconds"] = round(time.monotonic() - started, 3)
        write_json(result_path, summary)
    print(json.dumps({"status": summary["status"], "category": summary.get("category"), "result": str(result_path.relative_to(ROOT))}, ensure_ascii=False))
    return 0 if summary["status"] == "passed" else 1


def main():
    parser = argparse.ArgumentParser(description="Single-task HLS baseline evaluation; no repair or retry")
    parser.add_argument("manifest")
    parser.add_argument("--source", help="Use existing code for initial candidate; repairs may call API when enabled")
    parser.add_argument("--config")
    parser.add_argument("--cpu-only", action="store_true", help="Hide GPUs only from this run's tool subprocesses")
    parser.add_argument("--repair-attempts", type=int, default=0, help="Repair API calls after tool failure; baseline default is zero")
    args = parser.parse_args()
    if args.repair_attempts < 0 or args.repair_attempts > 5:
        parser.error("repair-attempts must be between 0 and 5")
    try:
        return evaluate(args)
    except (Failure, OSError):
        print("Cannot create result directory inside project", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
