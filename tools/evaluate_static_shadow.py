"""Freeze Bench4HLS candidates and run shadow A/B/C. Does not change baseline protocol.

Official Vitis/clang live on remote Linux (beida-server). Windows only prepares
the pack, copies it, and collects results. B/C never write candidates or tasks
and never feed the model.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent.core.contracts import digest, json_digest
from evaluation.static_check import HARD_RULES, RULES, result_dict, check_source, recover_contract
from evaluation.task_io import load_task
from serve.inference import write_json

DATASET = ROOT / "data" / "processed" / "bench4hls"
REMOTE_HOST = "beida-server"
REMOTE_VITIS = "/home/dingjy/sxt/zcomp-agent/vivado/2025.2/Vitis"
REMOTE_VIVADO = "/home/dingjy/sxt/zcomp-agent/vivado/2025.2/Vivado"
REMOTE_LICENSE = "/home/dingjy/sxt/zcomp-agent/vivado/vivado_license.lic"
REMOTE_WORK = "/home/dingjy/sxt/zcomp-ast-shadow"
ENV_CATEGORIES = {
    "environment_or_dependency_error", "license_error", "tool_timeout",
    "environment_error", "total_timeout",
}

# Tasks whose failure modes were read in prior reports; they stay in the dev split.
MANUAL_ANALYSIS = {
    "Prob016", "Prob021", "Prob022", "Prob023", "Prob034", "Prob036", "Prob039",
    "Prob040", "Prob043", "Prob050", "Prob064", "Prob082", "Prob088", "Prob099",
    "Prob101", "Prob103", "Prob104", "Prob105", "Prob116", "Prob121", "Prob130",
    "Prob131", "Prob146", "Prob152", "Prob162",
}
HARVEST_GLOBS = [
    "compare_*/Prob*/baseline_gen/candidate.cpp",
    "compare_*/Prob*/agent/candidate.cpp",
    "local_eval/**/artifacts/compare_*/Prob*/baseline_gen/candidate.cpp",
    "local_eval/**/artifacts/compare_*/Prob*/agent/candidate.cpp",
    "rerun_*/Prob*/baseline_gen/candidate.cpp",
    "rerun_*/Prob*/agent/candidate.cpp",
]


def sha_file(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def git_head():
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    except subprocess.CalledProcessError:
        return "unknown"


def task_ids():
    return sorted(p.name for p in DATASET.iterdir() if (p / "task.json").is_file())


def freeze_split(ids):
    dev = sorted(t for t in ids if t in MANUAL_ANALYSIS)
    holdout = sorted(t for t in ids if t not in MANUAL_ANALYSIS)
    return {
        "schema_version": 1,
        "dataset": "bench4hls",
        "split_unit": "task_id",
        "dev_tasks": dev,
        "holdout_tasks": holdout,
        "prior_eval_exposed_tasks": ids,
        "manual_analysis_tasks": sorted(MANUAL_ANALYSIS),
        "rule_development": "synthetic IR fixtures in tools/test_static_check.py; not tuned on holdout",
        "note": "All 170 Bench4HLS tasks appeared in prior agent reports. Holdout is a task-level split, not an independent benchmark.",
    }


def classify_prior(result_path):
    path = Path(result_path) if result_path else None
    if path is None or not path.is_file():
        return {"prior_status": "missing_result", "prior_category": None}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"prior_status": "unreadable_result", "prior_category": None}
    checks = data.get("checks") or {}
    category = data.get("category") or data.get("stop_reason")
    if category in ENV_CATEGORIES:
        return {"prior_status": "environment_anomaly", "prior_category": category}
    if checks.get("run") == "passed" and checks.get("synthesize") == "passed":
        return {"prior_status": "pass", "prior_category": None}
    if checks.get("synthesize") == "failed":
        return {"prior_status": "synthesis_fail", "prior_category": category}
    if checks.get("run") == "failed":
        return {"prior_status": "functional_fail", "prior_category": category}
    if checks.get("compile") == "failed":
        return {"prior_status": "compile_fail", "prior_category": category}
    return {"prior_status": "other", "prior_category": category}


def harvest_local(source_dir=None):
    found = []
    if source_dir:
        directory = Path(source_dir)
        for path in sorted(directory.glob("Prob*__*.cpp")):
            stem = path.stem
            if "__" not in stem:
                continue
            task_id, method = stem.split("__", 1)
            if not task_id.startswith("Prob") or method not in {"agent", "baseline"}:
                continue
            result = directory / (stem + ".result.json")
            if not result.is_file():
                result = None
            found.append((task_id, method, path, result))
        return found
    roots = [ROOT / "output"]
    for root in roots:
        if not root.is_dir():
            continue
        for pattern in HARVEST_GLOBS:
            for path in root.glob(pattern):
                task_id = next((p.name for p in path.parents if p.name.startswith("Prob")), None)
                if not task_id:
                    continue
                method = "agent" if "agent" in path.parts else "baseline"
                result = path.parent / "result.json"
                found.append((task_id, method, path, result if result.is_file() else None))
    return found


def origin_text(path):
    path = Path(path)
    try:
        return str(path.relative_to(ROOT)).replace("\\", "/")
    except ValueError:
        return str(path)


def ssh(host, command, timeout=30):
    return subprocess.run(
        ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", host, command],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
    )


def harvest_remote(host):
    command = (
        "python3 - <<'PY'\n"
        "from pathlib import Path\n"
        "root = Path('/home/dingjy/sxt/zcomp-agent/output')\n"
        "patterns = %r\n"
        "if root.is_dir():\n"
        "    for pattern in patterns:\n"
        "        for p in root.glob(pattern):\n"
        "            print(p)\n"
        "PY\n" % HARVEST_GLOBS
    )
    try:
        proc = ssh(host, command, timeout=60)
    except (subprocess.TimeoutExpired, OSError) as error:
        return [], str(error)
    if proc.returncode != 0:
        return [], proc.stderr[-2000:]
    rows = []
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line.endswith("candidate.cpp"):
            continue
        parts = Path(line).parts
        task_id = next((p for p in parts if str(p).startswith("Prob")), None)
        method = "agent" if "agent" in parts else "baseline"
        rows.append({"task_id": task_id, "method": method, "remote_path": line})
    return rows, ""


def freeze(args):
    ids = task_ids()
    if len(ids) != 170:
        print("warning: expected 170 Bench4HLS tasks, found", len(ids), flush=True)
    split = freeze_split(ids)
    local = harvest_local(getattr(args, "source_dir", None))
    remote, remote_error = ([], "skipped")
    if args.host and not getattr(args, "source_dir", None):
        remote, remote_error = harvest_remote(args.host)
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    out = ROOT / "output" / "static_shadow" / run_id
    out.mkdir(parents=True)
    sources = out / "sources"
    records = []
    for task_id, method, path, result in local:
        source = path.read_bytes()
        dest = sources / task_id / method / "candidate.cpp"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(source)
        prior = classify_prior(result)
        task = load_task(DATASET / task_id / "problem.txt", DATASET / task_id / "task.json")
        records.append({
            "task_id": task_id,
            "candidate_id": method,
            "source_sha256": digest(source),
            "split": "dev" if task_id in split["dev_tasks"] else "holdout",
            "source_path": str(dest.relative_to(out)).replace("\\", "/"),
            "origin_path": origin_text(path),
            "task_config_hash": task.fingerprint,
            "prior_result_path": origin_text(result) if result else None,
            "used_for_manual_analysis": task_id in MANUAL_ANALYSIS,
            **prior,
        })
    (out / "remote_candidates.json").write_text(json.dumps({
        "host": args.host, "error": remote_error, "candidates": remote,
    }, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    payload = {
        "schema_version": 1,
        "run_id": run_id,
        "run_dir": str(out.relative_to(ROOT)).replace("\\", "/"),
        "source_dir": getattr(args, "source_dir", None),
        "git_head": git_head(),
        "dataset": str(DATASET.relative_to(ROOT)).replace("\\", "/"),
        "remote": {
            "host": args.host,
            "vitis_root": REMOTE_VITIS,
            "vivado_root": REMOTE_VIVADO,
            "license_file": REMOTE_LICENSE,
        },
        "split": split,
        "candidates": records,
        "counts": {
            "tasks": len(ids),
            "dev_tasks": len(split["dev_tasks"]),
            "holdout_tasks": len(split["holdout_tasks"]),
            "local_candidates": len(records),
            "remote_candidate_paths": len(remote),
        },
        "missing": {
            "local_model_candidates": len(records) == 0,
            "remote_harvest_error": remote_error or None,
            "reason": None if records else "No Bench4HLS candidates in the source directory",
            "missing_baseline_or_agent": sorted({
                task for task in ids
                if {r["candidate_id"] for r in records if r["task_id"] == task} != {"agent", "baseline"}
            }),
        },
    }
    text = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
    (out / "candidate_list.json").write_text(text, encoding="utf-8")
    (out / "candidate_list.sha256").write_text(digest(text.encode()) + "\n", encoding="utf-8")
    write_json(out / "task_split.json", split)
    write_json(out / "experiment_config.json", {
        "git_head": git_head(),
        "policy": json.loads((ROOT / "agent/config/policy.json").read_text(encoding="utf-8")),
        "rules": RULES,
        "hard_rules": sorted(HARD_RULES),
        "contract_source": [
            "task.json top_function",
            "public tb.cpp FunctionDecl of that name",
        ],
        "remote": payload["remote"],
        "ld_library_path": library_path(REMOTE_VITIS).split(os.pathsep),
        "timing": {
            "a_start": "vitis-run process start",
            "a_csim_end": "csim_design process exit; compile and simulation are not split",
            "a_csynth_end": "csynth_design process exit; skipped if csim failed",
            "b_frontend": "libclang parse of candidate through diagnostics",
            "c_ast_rules": "IR walk on the same candidate TU; R02 is observational",
            "transfer_excluded": "scp/ssh setup is recorded separately and is not A/B/C compute time",
        },
    })
    print(json.dumps({"output": str(out.relative_to(ROOT)).replace("\\", "/"),
                      "local_candidates": len(records),
                      "remote_candidate_paths": len(remote),
                      "dev_tasks": len(split["dev_tasks"]),
                      "holdout_tasks": len(split["holdout_tasks"])}, ensure_ascii=False))
    return 0


def copy_task_inputs(work, task, source_bytes):
    inputs = work / "input"
    inputs.mkdir(parents=True)
    for material in task.materials:
        path = inputs / material.name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(material.content)
    source = inputs / task.manifest["source_file"]
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(source_bytes)
    return source


def run_scheme_a(task, source_bytes, runtime, work, deadline):
    from evaluation.hls import validate_stage
    copy_task_inputs(work, task, source_bytes)
    timings = {"prepare_seconds": 0.0, "csim_seconds": None, "csynth_seconds": None}
    stages = {}
    remaining = deadline - time.monotonic()
    csim = validate_stage("csim", work, task.manifest, runtime["hls"], True, remaining)
    timings["csim_seconds"] = csim.get("elapsed_seconds")
    stages["csim"] = {"status": csim["status"], "category": csim.get("category")}
    if csim["status"] == "passed":
        remaining = deadline - time.monotonic()
        syn = validate_stage("synthesis", work, task.manifest, runtime["hls"], True, remaining)
        timings["csynth_seconds"] = syn.get("elapsed_seconds")
        stages["synthesis"] = {"status": syn["status"], "category": syn.get("category")}
        passed = syn["status"] == "passed"
        category = syn.get("category")
    else:
        stages["synthesis"] = {"status": "not_run"}
        passed = False
        category = csim.get("category")
    env = category in ENV_CATEGORIES
    return {
        "status": "PASS" if passed else "FAIL",
        "environment_anomaly": env,
        "category": category,
        "stages": stages,
        "timings": timings,
    }


def library_path(vitis):
    return os.pathsep.join([
        vitis + "/lib/lnx64.o",
        vitis + "/lnx64/tools/clang-16/lib",
    ])


def prepare_runtime_env(runtime):
    prefix = [p for p in library_path(runtime["hls"]["vitis_root"]).split(os.pathsep) if p]
    current = [p for p in os.environ.get("LD_LIBRARY_PATH", "").split(os.pathsep) if p]
    seen = set()
    ordered = []
    for path in prefix + current:
        if path not in seen:
            seen.add(path)
            ordered.append(path)
    os.environ["LD_LIBRARY_PATH"] = os.pathsep.join(ordered)


def worker(args):
    pack = Path(args.pack).resolve()
    item = json.loads(Path(args.item).read_text(encoding="utf-8"))
    runtime = json.loads((pack / "runtime.remote.json").read_text(encoding="utf-8"))
    prepare_runtime_env(runtime)
    task_dir = pack / "tasks" / item["task_id"]
    task = load_task(task_dir / "problem.txt", task_dir / "task.json")
    source_path = pack / item["source_path"]
    source = source_path.read_bytes()
    if digest(source) != item["source_sha256"]:
        raise SystemExit("source hash mismatch before run")
    include_dirs = [task_dir / d for d in task.manifest["include_dirs"]]
    vitis = runtime["hls"]["vitis_root"]
    tb = task_dir / task.manifest["testbench_files"][0] if task.manifest["testbench_files"] else None
    contract = None
    if tb:
        try:
            contract = recover_contract(tb, task.manifest["top_function"], vitis, include_dirs,
                                        task.manifest["cxx_standard"])
        except Exception:
            contract = None
    work = pack / "work" / item["task_id"] / item["candidate_id"] / ("repeat_%d" % item["repeat"])
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    deadline = time.monotonic() + runtime["hls"]["total_timeout_seconds"]
    order = item["order"]
    results = {}
    for scheme in order:
        started = time.monotonic()
        if scheme == "A":
            results["A"] = run_scheme_a(task, source, runtime, work / "A", deadline)
        elif scheme == "B":
            results["B"] = result_dict(check_source(source_path, tb, task.manifest["top_function"],
                                                    vitis, include_dirs, task.manifest["cxx_standard"],
                                                    with_rules=False, contract=contract))
        elif scheme == "C":
            results["C"] = result_dict(check_source(source_path, tb, task.manifest["top_function"],
                                                    vitis, include_dirs, task.manifest["cxx_standard"],
                                                    with_rules=True, contract=contract))
        results[scheme]["wall_seconds"] = round(time.monotonic() - started, 6)
    if digest(source_path.read_bytes()) != item["source_sha256"]:
        raise SystemExit("source hash mismatch after run")
    out = work / "result.json"
    write_json(out, {"item": item, "results": results})
    print(str(out))
    return 0


def orders(index, repeat):
    perms = ["ABC", "ACB", "BAC", "BCA", "CAB", "CBA"]
    return list(perms[(index + repeat) % 6])


def pack_and_note(args):
    listing = json.loads(Path(args.list).read_text(encoding="utf-8"))
    pack = Path(args.pack)
    pack.mkdir(parents=True, exist_ok=True)
    shutil.copytree(DATASET, pack / "tasks", dirs_exist_ok=True)
    sources = ROOT / listing["run_dir"] / "sources" if listing.get("run_dir") else None
    if sources and sources.is_dir():
        shutil.copytree(sources, pack / "sources", dirs_exist_ok=True)
    write_json(pack / "runtime.remote.json", {
        "model": {"base_url": "http://127.0.0.1:9/v1", "name": "unused", "max_tokens": 16,
                  "temperature": 0, "enable_thinking": False, "timeout_seconds": 1,
                  "context_tokens": 1024, "context_note": "shadow validation does not call the model",
                  "tls_sha256": ""},
        "hls": {
            "vitis_root": REMOTE_VITIS,
            "vivado_root": REMOTE_VIVADO,
            "license_file": REMOTE_LICENSE,
            "part": "xczu3eg-sbva484-1-e",
            "clock_ns": 5,
            "csim_timeout_seconds": 120,
            "synthesis_timeout_seconds": 300,
            "total_timeout_seconds": 600,
        },
    })
    write_json(pack / "candidate_list.json", listing)
    for rel in ["evaluation/__init__.py", "evaluation/hls.py", "evaluation/static_check.py",
                "evaluation/task_io.py", "serve/__init__.py", "serve/inference.py",
                "agent/__init__.py", "agent/core/__init__.py", "agent/core/contracts.py",
                "tools/evaluate_static_shadow.py"]:
        dest = pack / "harness" / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / rel, dest)
    print(json.dumps({"pack": str(pack)}))
    return 0


def write_items(pack, listing, repeats):
    items = []
    directory = pack / "items"
    directory.mkdir(parents=True, exist_ok=True)
    for index, candidate in enumerate(listing["candidates"]):
        for repeat in range(repeats):
            item = dict(candidate)
            item.update(repeat=repeat, order=orders(index, repeat), index=index)
            name = "%s_%s_%d.json" % (candidate["task_id"], candidate["candidate_id"], repeat)
            path = directory / name
            path.write_text(json.dumps(item, ensure_ascii=False) + "\n", encoding="utf-8")
            items.append(path)
    return items


def batch(args):
    pack = Path(args.pack).resolve()
    listing = json.loads((pack / "candidate_list.json").read_text(encoding="utf-8"))
    items = write_items(pack, listing, args.repeats)
    if args.limit:
        items = items[: args.limit]
    progress = pack / "progress.json"
    done = 0
    for path in items:
        item = json.loads(path.read_text(encoding="utf-8"))
        result = pack / "work" / item["task_id"] / item["candidate_id"] / ("repeat_%d" % item["repeat"]) / "result.json"
        if result.is_file() and not args.force:
            done += 1
            continue
        code = worker(argparse.Namespace(pack=str(pack), item=str(path)))
        if code != 0:
            return code
        done += 1
        write_json(progress, {"done": done, "total": len(items), "last": path.name})
        print(json.dumps({"done": done, "total": len(items), "item": path.name}), flush=True)
    return summarize(argparse.Namespace(dir=str(pack)))


def scp(local, remote, host, timeout=120):
    return subprocess.run(
        ["scp", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "-r", str(local), host + ":" + remote],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
    )


def run_remote(args):
    listing = json.loads(Path(args.list).read_text(encoding="utf-8"))
    if not listing.get("candidates"):
        print(json.dumps({"status": "no_candidates",
                          "message": "Holdout/dev candidate harvest is empty; not inventing model sources"}))
        return 2
    pack = Path(args.pack)
    pack_and_note(argparse.Namespace(list=args.list, pack=str(pack)))
    write_items(pack, listing, args.repeats)
    remote_root = args.remote_dir.rstrip("/")
    remote_pack = remote_root + "/" + pack.name
    mkdir = ssh(args.host, "mkdir -p " + remote_root + " && rm -rf " + remote_pack, timeout=30)
    if mkdir.returncode != 0:
        print(mkdir.stderr)
        return mkdir.returncode
    copied = scp(pack, remote_pack, args.host, timeout=600)
    if copied.returncode != 0:
        print(copied.stderr)
        return copied.returncode
    limit = (" --limit %d" % args.limit) if args.limit else ""
    log = remote_pack + "/batch.log"
    command = (
        "export LD_LIBRARY_PATH=%s:${LD_LIBRARY_PATH}; "
        "cd %s/harness && PYTHONPATH=%s/harness nohup python3 -B tools/evaluate_static_shadow.py "
        "batch --pack %s --repeats %d%s > %s 2>&1 & echo $!"
        % (library_path(REMOTE_VITIS), remote_pack, remote_pack, remote_pack, args.repeats, limit, log)
    )
    proc = ssh(args.host, command, timeout=30)
    print(json.dumps({"remote_pack": remote_pack, "pid": proc.stdout.strip(),
                      "log": log, "stderr": proc.stderr[-500:]}, ensure_ascii=False))
    return proc.returncode


def runtime_timeout():
    return 700


def summarize(args):
    root = Path(args.dir)
    rows = []
    for path in sorted(root.glob("work/*/*/repeat_*/result.json")):
        rows.append(json.loads(path.read_text(encoding="utf-8")))
    (root / "raw_results.json").write_text(json.dumps(rows, indent=2, ensure_ascii=False) + "\n",
                                           encoding="utf-8")
    print(json.dumps({"results": len(rows), "path": str((root / "raw_results.json"))}))
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    freeze_cmd = sub.add_parser("freeze")
    freeze_cmd.add_argument("--host", default=REMOTE_HOST)
    freeze_cmd.add_argument("--source-dir", help="Flattened ProbXXX__agent.cpp directory")
    freeze_cmd.set_defaults(func=freeze)
    worker_cmd = sub.add_parser("worker")
    worker_cmd.add_argument("--pack", required=True)
    worker_cmd.add_argument("--item", required=True)
    worker_cmd.set_defaults(func=worker)
    batch_cmd = sub.add_parser("batch")
    batch_cmd.add_argument("--pack", required=True)
    batch_cmd.add_argument("--repeats", type=int, default=1)
    batch_cmd.add_argument("--limit", type=int, default=0)
    batch_cmd.add_argument("--force", action="store_true")
    batch_cmd.set_defaults(func=batch)
    pack_cmd = sub.add_parser("pack")
    pack_cmd.add_argument("--list", required=True)
    pack_cmd.add_argument("--pack", required=True)
    pack_cmd.set_defaults(func=pack_and_note)
    run_cmd = sub.add_parser("run")
    run_cmd.add_argument("--list", required=True)
    run_cmd.add_argument("--pack", required=True)
    run_cmd.add_argument("--host", default=REMOTE_HOST)
    run_cmd.add_argument("--remote-dir", default=REMOTE_WORK)
    run_cmd.add_argument("--repeats", type=int, default=1)
    run_cmd.add_argument("--limit", type=int, default=0)
    run_cmd.set_defaults(func=run_remote)
    sum_cmd = sub.add_parser("summarize")
    sum_cmd.add_argument("--dir", required=True)
    sum_cmd.set_defaults(func=summarize)
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
