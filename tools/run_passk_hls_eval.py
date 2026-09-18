"""Concurrent Vitis HLS graded evaluation for a pass@k validation pack."""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import time
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parent
MANIFEST = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))
VITIS = Path(MANIFEST.get("vitis_root", "/home/dingjy/sxt/zcomp-agent/vivado/2025.2/Vitis"))
VIVADO = VITIS.parent / "Vivado"
LICENSE = MANIFEST.get("license_file", "/home/dingjy/sxt/zcomp-agent/vivado/vivado_license.lic")


def write(path, obj):
    tmp = Path(path).with_suffix(Path(path).suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=2), encoding="utf-8")
    tmp.replace(path)


def execute(args, cwd, env, log, timeout):
    started = time.monotonic()
    timed_out = False
    with log.open("wb") as output:
        proc = subprocess.Popen(args, cwd=cwd, env=env, stdout=output, stderr=subprocess.STDOUT, start_new_session=True)
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            proc.wait()
    return dict(exit_code=proc.returncode, timed_out=timed_out, seconds=round(time.monotonic() - started, 3),
                log=str(log.relative_to(ROOT)))


def ok(record):
    return record["exit_code"] == 0 and not record["timed_out"]


def tcl(value):
    if any(character in str(value) for character in "{}\n\r"):
        raise ValueError("Invalid Tcl word")
    return "{" + str(value) + "}"


def setup_lines(task, work):
    flags = "-std=c++14 -I" + str(work / "input")
    lines = ["open_project " + tcl(work / "project"), "set_top " + task["top"],
             "add_files " + tcl(work / "input" / task["source"]) + " -cflags " + tcl(flags),
             "add_files -tb " + tcl(work / "input" / task["testbench"]) + " -cflags " + tcl(flags)]
    for name in task["tb_data"]:
        lines.append("add_files -tb " + tcl(work / "input" / name))
    lines += ["open_solution -flow_target vivado solution1", "set_part " + tcl(MANIFEST["part"]),
              "create_clock -period " + str(MANIFEST["clock_ns"]) + " -name default"]
    return lines


def stage_tcl(task, work, env, stage, command, timeout):
    script = work / (stage + ".tcl")
    script.write_text("\n".join(setup_lines(task, work) + [command, "puts FROZEN_STAGE_OK", "exit"]) + "\n")
    result = execute([str(VITIS / "bin/vitis-run"), "--mode", "hls", "--tcl", str(script)], work, env,
                     work / (stage + ".log"), timeout)
    text = (work / (stage + ".log")).read_text(errors="replace")
    result["passed"] = ok(result) and "FROZEN_STAGE_OK" in text and not re.search(r"^ERROR:", text, re.M)
    result["diagnostics"] = "\n".join(line for line in text.splitlines()
                                      if re.search(r"error:|ERROR:|fatal|mismatch", line, re.I))[-5000:]
    return result


def env_for(work):
    env = os.environ.copy()
    for name in ["LLM_API_KEY", "DASHSCOPE_API_KEY"]:
        env.pop(name, None)
    env.update(XILINX_VITIS=str(VITIS), XILINX_HLS=str(VITIS), XILINX_VIVADO=str(VIVADO),
               XILINXD_LICENSE_FILE=LICENSE, OMP_NUM_THREADS="1", OPENBLAS_NUM_THREADS="1")
    libs = [VITIS / "lib/lnx64.o", VITIS / "lnx64/lib/csim", VITIS / "lnx64/tools/gcc/lib64",
            VITIS / "lnx64/tools/clang-16/lib"]
    env["LD_LIBRARY_PATH"] = ":".join(map(str, libs)) + ":" + env.get("LD_LIBRARY_PATH", "")
    env["PATH"] = str(VITIS / "bin") + ":" + str(VIVADO / "bin") + ":" + env.get("PATH", "")
    tmp = work / "tmp"
    tmp.mkdir(exist_ok=True)
    env["TMPDIR"] = str(tmp)
    return env


def compile_run(task, work, reference=False):
    work.mkdir(parents=True, exist_ok=False)
    shutil.copytree(ROOT / "inputs" / task["task_id"], work / "input")
    if reference:
        shutil.copyfile(ROOT / "references" / task["task_id"] / task["source"], work / "input" / task["source"])
    env = env_for(work)
    compile_result = stage_tcl(task, work, env, "compile", "csim_design -setup", MANIFEST["compile_timeout"])
    binary = work / "project/solution1/csim/build/csim.exe"
    compile_result["passed"] = compile_result["passed"] and binary.is_file()
    result = dict(compile=compile_result)
    if not compile_result["passed"]:
        return result, env
    runtime = execute([str(binary)], binary.parent, env, work / "run.log", MANIFEST["run_timeout"])
    runtime["passed"] = ok(runtime)
    result["run"] = runtime
    if reference and runtime["passed"]:
        repeated = execute([str(binary)], binary.parent, env, work / "run_repeat.log", MANIFEST["run_timeout"])
        result["deterministic"] = ok(repeated) and (work / "run.log").read_bytes() == (work / "run_repeat.log").read_bytes()
    return result, env


def evaluate(task):
    root = ROOT / "results" / task["task_id"]
    result_file = root / "result.json"
    if result_file.is_file():
        return json.loads(result_file.read_text(encoding="utf-8"))
    root.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    result = dict(task_id=task["task_id"], original_task_id=task.get("original_task_id"), sample=task.get("sample"),
                  status="running", checks={stage: "not_run" for stage in ["parse", "compile", "run", "synthesize"]},
                  source_sha256=task["source_sha256"], model_requests=0, source_modified=False)
    write(result_file, result)
    try:
        source = ROOT / "inputs" / task["task_id"] / task["source"]
        assert hashlib.sha256(source.read_bytes()).hexdigest() == task["source_sha256"]
        candidate_work = root / "candidate"
        candidate, env = compile_run(task, candidate_work)
        result["candidate"] = candidate
        if not candidate["compile"]["passed"]:
            result["checks"].update(parse="unknown", compile="failed")
            result["status"] = "compile_failed"
            return result
        result["checks"].update(parse="passed", compile="passed")
        if not candidate["run"]["passed"]:
            result["checks"]["run"] = "failed"
            result["status"] = "runtime_failed"
            return result
        reference_work = root / "reference"
        reference, _ = compile_run(task, reference_work, reference=True)
        result["reference"] = reference
        if not reference["compile"]["passed"] or not reference.get("run", {}).get("passed"):
            result["checks"]["run"] = "unknown"
            result["status"] = "reference_or_environment_failed"
            return result
        if not reference.get("deterministic"):
            result["checks"]["run"] = "unknown"
            result["status"] = "reference_nondeterministic"
            return result
        expected = (reference_work / "run.log").read_bytes()
        actual = (candidate_work / "run.log").read_bytes()
        result["comparison"] = dict(method="exit_zero_and_exact_testbench_stdout_stderr",
                                    expected_sha256=hashlib.sha256(expected).hexdigest(),
                                    actual_sha256=hashlib.sha256(actual).hexdigest(),
                                    reference_output_bytes=len(expected))
        if actual != expected:
            result["checks"]["run"] = "failed"
            result["status"] = "output_mismatch"
            return result
        result["checks"]["run"] = "passed"
        synthesis = stage_tcl(task, candidate_work, env, "synthesis", "csynth_design", MANIFEST["synth_timeout"])
        xml = candidate_work / "project/solution1/syn/report" / (task["top"] + "_csynth.xml")
        rtl = list((candidate_work / "project/solution1/syn/verilog").glob("*.v"))
        synthesis["passed"] = synthesis["passed"] and xml.is_file() and bool(rtl)
        if synthesis["passed"]:
            tree = ET.parse(xml).getroot()
            synthesis["estimates"] = {name: tree.findtext(path) for name, path in {
                "clock_ns": ".//EstimatedClockPeriod", "lut": ".//AreaEstimates/Resources/LUT",
                "ff": ".//AreaEstimates/Resources/FF", "dsp": ".//AreaEstimates/Resources/DSP",
                "bram": ".//AreaEstimates/Resources/BRAM_18K"}.items()}
        result["synthesis"] = synthesis
        result["checks"]["synthesize"] = "passed" if synthesis["passed"] else "failed"
        result["status"] = "passed" if synthesis["passed"] else "synthesis_failed"
    except Exception as error:
        result.update(status="evaluation_error", error=repr(error))
    finally:
        result["elapsed_seconds"] = round(time.monotonic() - started, 3)
        write(result_file, result)
    return result


def passk(results, level, task_count, samples):
    slots = task_count * samples
    by_task = {}
    for record in results:
        task = record.get("original_task_id") or record["task_id"].rsplit("/sample_", 1)[0]
        by_task.setdefault(task, 0)
        if record["checks"].get(level) == "passed":
            by_task[task] += 1
    passed_slots = sum(by_task.values())
    passed_tasks = sum(1 for count in by_task.values() if count)
    # Missing original tasks (no candidate) stay at 0 and still belong in the denominators.
    return {"passed_slots": passed_slots, "pass_at_1": passed_slots / slots,
            "tasks_with_pass": passed_tasks, "pass_at_5": passed_tasks / task_count}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--workers", type=int, default=16)
    parser.add_argument("--task")
    args = parser.parse_args()
    if args.workers < 1:
        raise ValueError("workers must be positive")
    tasks = [task for task in MANIFEST["tasks"] if args.task is None or task["task_id"] == args.task]
    summary = dict(status="running", task_count=len(tasks), completed=0, results=[], workers=args.workers,
                   note="Vitis HLS 2025.2 graded eval; parse inferred from csim setup. Exact reference testbench bytes; no hidden tests.",
                   started_at=datetime.now(timezone.utc).isoformat())
    summary_file = ROOT / ("pilot_summary.json" if args.task else "summary.json")
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(evaluate, task) for task in tasks]
        for future in as_completed(futures):
            record = future.result()
            summary["results"].append(record)
            summary["completed"] += 1
            write(summary_file, summary)
            print(json.dumps(dict(task=record["task_id"], status=record["status"], checks=record["checks"],
                                  completed=summary["completed"], total=len(tasks))), flush=True)
    summary["status"] = "completed"
    summary["finished_at"] = datetime.now(timezone.utc).isoformat()
    summary["counts"] = {stage: sum(record["checks"][stage] == "passed" for record in summary["results"])
                         for stage in ["parse", "compile", "run", "synthesize"]}
    if args.task is None:
        summary["passk"] = {
            level: passk(summary["results"], level, MANIFEST["task_count"], MANIFEST["samples"])
            for level in ["parse", "compile", "run", "synthesize"]
        }
        summary["passk_note"] = "pass@1 denominator is all original slots; missing candidates count as failures."
    write(summary_file, summary)
    print(json.dumps(summary["counts"]), flush=True)


if __name__ == "__main__":
    main()
