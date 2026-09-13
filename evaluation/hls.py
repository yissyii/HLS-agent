import os
from pathlib import Path
import re
import shlex
import signal
import subprocess
import time
import xml.etree.ElementTree as ET

from serve.inference import Failure, project_path


def tcl_word(value):
    value = str(value)
    for character in ["\\", '"', "$", "[", "]"]:
        value = value.replace(character, "\\" + character)
    return '"' + value.replace("\n", "\\n").replace("\r", "\\r") + '"'


def environment(work, settings, cpu_only):
    vitis = Path(settings["vitis_root"]).resolve()
    vivado = Path(settings["vivado_root"]).resolve()
    executable = vitis / ("bin/vitis-run.bat" if os.name == "nt" else "bin/vitis-run")
    if not executable.is_file() or not os.access(executable, os.X_OK):
        raise Failure("environment_error", "Vitis executable not found or not executable")
    values = os.environ.copy()
    if os.name == "nt" and not values.get("PROCESSOR_ARCHITECTURE"):
        # Some automation hosts omit this variable; AMD loader.bat otherwise
        # exits with code 1 and suppresses its unsupported-architecture message.
        import ctypes
        system_info = ctypes.create_string_buffer(64)
        ctypes.windll.kernel32.GetNativeSystemInfo(system_info)
        architecture = ctypes.cast(system_info, ctypes.POINTER(ctypes.c_ushort))[0]
        values["PROCESSOR_ARCHITECTURE"] = {9: "AMD64", 12: "ARM64", 0: "x86"}.get(architecture, "unknown")
    values.pop("DASHSCOPE_API_KEY", None)
    values.pop("LLM_API_KEY", None)
    for name, directory in [("HOME", "home"), ("TMPDIR", "tmp"), ("TMP", "tmp"), ("TEMP", "tmp"), ("XDG_CACHE_HOME", "cache"), ("XDG_CONFIG_HOME", "config"), ("XDG_DATA_HOME", "data")]:
        location = work / directory
        location.mkdir(exist_ok=True)
        values[name] = str(location)
    values.update(XILINX_VITIS=str(vitis), XILINX_HLS=str(vitis), XILINX_VIVADO=str(vivado))
    values["PATH"] = str(vitis / "bin") + os.pathsep + str(vivado / "bin") + os.pathsep + values.get("PATH", "")
    if settings["license_file"]:
        values["XILINXD_LICENSE_FILE"] = str(Path(settings["license_file"]).resolve())
    if cpu_only:
        for name in ["CUDA_VISIBLE_DEVICES", "HIP_VISIBLE_DEVICES", "ROCR_VISIBLE_DEVICES", "GPU_DEVICE_ORDINAL"]:
            values[name] = "-1"
    return executable, values


def run_process(command, work, environment_values, log, timeout):
    started = time.monotonic()
    timed_out = False
    with log.open("w", encoding="utf-8") as stream:
        options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {"start_new_session": True}
        process = subprocess.Popen(command, cwd=work, env=environment_values, stdout=stream, stderr=subprocess.STDOUT, **options)
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            if os.name == "nt":
                subprocess.run(["taskkill.exe", "/PID", str(process.pid), "/T", "/F"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW, timeout=15)
                process.wait(timeout=15)
                return {"exit_code": process.returncode, "timed_out": True, "elapsed_seconds": round(time.monotonic() - started, 3), "log": log.name}
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                pass
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
        except BaseException:
            if os.name == "nt":
                subprocess.run(["taskkill.exe", "/PID", str(process.pid), "/T", "/F"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW, timeout=15)
                process.wait(timeout=15)
                raise
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
            raise
    return {"exit_code": process.returncode, "timed_out": timed_out, "elapsed_seconds": round(time.monotonic() - started, 3), "log": log.name}


def classify(stage, execution, text):
    lowered = text.lower()
    if execution["timed_out"]:
        return "tool_timeout"
    if any(value in lowered for value in ["license checkout failed", "failed to acquire license", "no valid license", "license check failed", "failed to check out license"]):
        return "license_error"
    if any(value in lowered for value in ["error while loading shared libraries", "command not found", "no such file or directory", "permission denied"]):
        return "environment_or_dependency_error"
    if stage == "synthesis":
        return "synthesis_error"
    compiler_error = re.search(r"^(?!\s*error:\s*\[).*\b(?:fatal )?error:|undefined reference", lowered, re.MULTILINE)
    if compiler_error:
        return "compile_error"
    if "csim.exe" in lowered and any(value in lowered for value in ["linking", "running", "generating"]):
        return "functional_or_runtime_error"
    return "compile_or_csim_error"


def validate_stage(stage, work, manifest, settings, cpu_only, budget):
    executable, values = environment(work, settings, cpu_only)
    flags = "-std=" + manifest["cxx_standard"]
    for directory in manifest["include_dirs"]:
        flags += ' -I"' + (work / "input" / directory).as_posix() + '"'
    lines = ["open_project hls_project", "set_top " + tcl_word(manifest["top_function"])]
    for source in [manifest["source_file"]] + manifest["design_files"]:
        lines.append("add_files " + tcl_word((work / "input" / source).as_posix()) + " -cflags " + tcl_word(flags))
    for source in manifest["testbench_files"]:
        lines.append("add_files -tb " + tcl_word((work / "input" / source).as_posix()) + " -cflags " + tcl_word(flags))
    for source in manifest["support_files"]:
        lines.append("add_files -tb " + tcl_word((work / "input" / source).as_posix()))
    lines.extend(["open_solution -flow_target vivado solution1", "set_part " + tcl_word(settings["part"]), "create_clock -period " + str(settings["clock_ns"]) + " -name default"])
    lines.extend(["csim_design" if stage == "csim" else "csynth_design", "puts ZCOMP_STAGE_SUCCESS_" + stage.upper(), "exit"])
    script = work / (stage + ".tcl")
    script.write_text("\n".join(lines) + "\n", encoding="utf-8")
    limit = settings["csim_timeout_seconds" if stage == "csim" else "synthesis_timeout_seconds"]
    execution = run_process([str(executable), "--mode", "hls", "--tcl", str(script)], work, values, work / (stage + ".log"), min(limit, budget))
    text = (work / execution["log"]).read_text(encoding="utf-8", errors="replace")
    marker = "ZCOMP_STAGE_SUCCESS_" + stage.upper()
    passed = execution["exit_code"] == 0 and not execution["timed_out"] and marker in text and not re.search(r"^ERROR:", text, re.MULTILINE)
    if stage == "synthesis":
        report = work / "hls_project/solution1/syn/report" / (manifest["top_function"] + "_csynth.xml")
        rtl = list((work / "hls_project/solution1/syn/verilog").glob("*.v"))
        passed = passed and report.is_file() and bool(rtl)
        if passed:
            try:
                document = ET.parse(report).getroot()
                execution["estimates"] = {name: document.findtext(path) for name, path in {
                    "clock_ns": ".//SummaryOfTimingAnalysis/EstimatedClockPeriod",
                    "latency_cycles_min": ".//SummaryOfOverallLatency/Best-caseLatency",
                    "latency_cycles_max": ".//SummaryOfOverallLatency/Worst-caseLatency",
                    "lut": ".//AreaEstimates/Resources/LUT",
                    "ff": ".//AreaEstimates/Resources/FF",
                    "dsp": ".//AreaEstimates/Resources/DSP",
                    "bram_18k": ".//AreaEstimates/Resources/BRAM_18K",
                }.items()}
                execution["report"] = str(report.relative_to(work))
            except (ET.ParseError, OSError):
                passed = False
    execution["status"] = "passed" if passed else "failed"
    if not passed:
        execution["category"] = classify(stage, execution, text)
        diagnostics = [line for line in text.splitlines() if re.search(r"error:|ERROR:|FAIL|mismatch|fatal", line, re.IGNORECASE)]
        execution["diagnostic_tail"] = ("\n".join(diagnostics[:30]) + "\n" + "\n".join(text.splitlines()[-12:]))[-6000:]
    return execution
