"""Build a Vitis validation pack from a pass@k baseline generation run."""
import argparse
import hashlib
import json
import shutil
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT.parent / "hls-overlap-audit" / "hls-eval" / "hls_eval_data"
EVAL_SCRIPT = ROOT / "tools" / "run_passk_hls_eval.py"


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def good(record):
    return record.get("exit_code") == 0 and (record.get("result") or {}).get("source") == "candidate.cpp"


def candidate_path(run, record):
    return Path(run) / "tasks" / record["task_id"] / f"sample_{record['sample']}" / "candidate.cpp"


def select_candidates(runs):
    selected = {}
    sources = {}
    for run in runs:
        manifest = read(Path(run) / "manifest.json")
        for record in manifest.get("results", []):
            if not good(record):
                continue
            source = candidate_path(run, record)
            if not source.is_file():
                continue
            key = (record["task_id"], record["sample"])
            selected[key] = source
            sources[key] = str(source)
    return selected, sources


def gold_cpp(task_dir):
    files = [p for p in task_dir.iterdir() if p.suffix == ".cpp" and not p.name.endswith("_tb.cpp")]
    if len(files) != 1:
        raise FileNotFoundError(f"expected one gold kernel in {task_dir}, found {[p.name for p in files]}")
    return files[0]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="append", required=True, help="Generation output directory; later runs overlay earlier ones")
    parser.add_argument("--output", required=True)
    parser.add_argument("--dataset", default=str(DATA))
    args = parser.parse_args()
    out = Path(args.output).resolve()
    if not out.is_relative_to(ROOT / "output") or out.exists():
        raise ValueError("new output directory under output/ required")
    dataset = Path(args.dataset).resolve()
    primary = read(Path(args.run[0]) / "manifest.json")
    selected, origins = select_candidates(args.run)
    out.mkdir(parents=True)
    tasks = []
    for (task_id, sample), source in sorted(selected.items()):
        task_dir = dataset / task_id
        slot = f"{task_id}/sample_{sample}"
        dest = out / "inputs" / slot
        dest.mkdir(parents=True)
        testbench = next(task_dir.glob("*_tb.cpp"))
        config = tomllib.loads((task_dir / "hls_eval_config.toml").read_text(encoding="utf-8"))
        deps = [testbench]
        deps += [p for p in task_dir.iterdir() if p.suffix in {".h", ".hpp", ".hh"}]
        deps += [task_dir / name for name in config.get("tb_data", [])]
        shutil.copyfile(source, dest / "candidate.cpp")
        for path in deps:
            shutil.copyfile(path, dest / path.name)
        reference = out / "references" / slot
        reference.mkdir(parents=True)
        shutil.copyfile(gold_cpp(task_dir), reference / "candidate.cpp")
        tasks.append({
            "task_id": slot,
            "original_task_id": task_id,
            "sample": sample,
            "source": "candidate.cpp",
            "testbench": testbench.name,
            "top": (task_dir / "top.txt").read_text(encoding="utf-8").strip(),
            "tb_data": config.get("tb_data", []),
            "source_sha256": digest(source),
            "source_origin": origins[(task_id, sample)],
            "dependencies": {path.name: digest(path) for path in deps},
        })
    shutil.copyfile(EVAL_SCRIPT, out / EVAL_SCRIPT.name)
    manifest = {
        "task_count": primary["task_count"],
        "samples": primary["samples"],
        "candidates": len(tasks),
        "generation_runs": [str(Path(run).resolve()) for run in args.run],
        "part": "xczu3eg-sbva484-1-e",
        "clock_ns": 5,
        "cxx_standard": "c++14",
        "compile_timeout": 180,
        "run_timeout": 120,
        "synth_timeout": 300,
        "source_mutations": False,
        "model_requests": 0,
        "vitis_root": "/home/dingjy/sxt/zcomp-agent/vivado/2026.1/Vitis",
        "license_file": "/home/dingjy/sxt/zcomp-agent/vivado/vivado_license.lic",
        "records": tasks,
        "tasks": tasks,
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(out), "candidates": len(tasks), "slots": primary["task_count"] * primary["samples"]}))


if __name__ == "__main__":
    main()
