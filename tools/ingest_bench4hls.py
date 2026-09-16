"""Ingest the public Bench4HLS benchmark into framework task manifests.

Downloads the pinned Bench4HLS snapshot (github.com/zfsadik/Bench4HLS) into
``data/raw/Bench4HLS/`` and converts each of its 170 case studies into a task
directory under ``data/processed/bench4hls/ProbNNN/`` containing:

    problem.txt  -- natural-language instruction (passed verbatim to the model)
    task.json    -- manifest understood by evaluation/task_io.py
    tb.cpp       -- self-checking testbench, adapted so main() returns nonzero
                    on mismatch (Bench4HLS testbenches print "Test Failed" but
                    always ``return 0``, which this harness's csim check would
                    otherwise misread as a pass)

Reference designs are intentionally NOT copied into ``data/processed/``: the
testbench is the only validator, and the harness forbids committing reference
implementations under ``data/``.

Usage:  python -B tools/ingest_bench4hls.py [--force]
"""
import argparse
import io
import json
import re
from pathlib import Path
import sys
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw" / "Bench4HLS"
PROCESSED = ROOT / "data" / "processed" / "bench4hls"

REPO = "zfsadik/Bench4HLS"
COMMIT = "7fac5b356b0383e9463995e2c6b13a5fee27a62d"
ARCHIVE_URL = f"https://github.com/{REPO}/archive/{COMMIT}.zip"

TOP_FUNCTION = "TopModule"

_PASS_GUARD = re.compile(
    r'if\s*\(\s*([A-Za-z_]\w*)\s*==\s*0\s*\)\s*\{[^\n]*\n\s*std::cout\s*<<\s*"Test Passed',
    re.DOTALL,
)
_ZERO_RETURNS = re.compile(r'if\s*\(\s*([A-Za-z_]\w*)\s*==\s*0\s*\)')


def log(message):
    print(message, file=sys.stderr)


def download_archive():
    if (RAW / "benchmark" / "input_prompts.json").is_file():
        log(f"Reusing existing snapshot at {RAW}")
        return
    RAW.mkdir(parents=True, exist_ok=True)
    log(f"Downloading {ARCHIVE_URL}")
    with urllib.request.urlopen(ARCHIVE_URL, timeout=120) as response:
        payload = response.read()
    log(f"Downloaded {len(payload)} bytes; extracting")
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        top = archive.namelist()[0].split("/")[0]
        for member in archive.namelist():
            if not member.startswith(top + "/") or member.endswith("/"):
                continue
            target = RAW / member[len(top) + 1:]
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(archive.read(member))
    (RAW / "COMMIT").write_text(COMMIT + "\n", encoding="utf-8")
    log(f"Extracted snapshot to {RAW}")


def adapt_testbench(source):
    """Return a testbench that returns nonzero on mismatch, or None if unsafe."""
    guard = _PASS_GUARD.search(source)
    if guard:
        counter = guard.group(1)
    else:
        before_fail = source[: source.rfind("Test Failed")] if "Test Failed" in source else source
        candidates = _ZERO_RETURNS.findall(before_fail)
        counter = candidates[-1] if candidates else None
    if counter is None:
        return None
    # main()'s exit is the final ``return 0;`` immediately before the trailing ``}``.
    index = source.rfind("return 0;")
    if index == -1:
        return None
    return (
        source[:index]
        + f"return ({counter} == 0) ? 0 : 1;"
        + source[index + len("return 0;"):]
    )


def prompt_text(task):
    # The prompt dirs mirror input_prompts.json; prefer the canonical txt file.
    path = RAW / "benchmark" / "prompts" / f"{task['task_n']}_prompt.txt"
    if path.is_file():
        return path.read_text(encoding="utf-8").rstrip("\n")
    return task["input"].rstrip("\n")


def convert_all(force):
    prompts_path = RAW / "benchmark" / "input_prompts.json"
    if not prompts_path.is_file():
        raise SystemExit("Bench4HLS snapshot not found; run the download step first")
    prompts = json.loads(prompts_path.read_text(encoding="utf-8"))
    if force and PROCESSED.exists():
        import shutil
        shutil.rmtree(PROCESSED)
    PROCESSED.mkdir(parents=True, exist_ok=True)

    index = []
    unadapted = []
    for task in prompts:
        name = task["task_n"]  # e.g. Prob001
        directory = PROCESSED / name
        directory.mkdir(parents=True, exist_ok=True)
        problem = prompt_text(task)
        (directory / "problem.txt").write_text(problem + "\n", encoding="utf-8")

        testbench = (RAW / "benchmark" / "testbenches" / f"{name}_tb.cpp").read_text(encoding="utf-8")
        adapted = adapt_testbench(testbench)
        if adapted is None:
            unadapted.append(name)
            (directory / "tb.cpp").write_bytes(testbench.encode("utf-8"))
        else:
            (directory / "tb.cpp").write_bytes(adapted.encode("utf-8"))

        manifest = {
            "id": name,
            "top_function": TOP_FUNCTION,
            "problem_file": "problem.txt",
            "source_file": "kernel.cpp",
            "testbench_files": ["tb.cpp"],
            "cxx_standard": "c++14",
            "feedback_policy": "category_only",
        }
        (directory / "task.json").write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        index.append({"id": name, "top_function": TOP_FUNCTION,
                      "testbench_adapted": adapted is not None})

    (PROCESSED / "index.json").write_text(
        json.dumps({
            "source": {"repo": REPO, "commit": COMMIT, "url": ARCHIVE_URL},
            "task_count": len(prompts),
            "top_function": TOP_FUNCTION,
            "tasks": index,
            "unadapted_testbenches": unadapted,
        }, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    adapted_count = len(index) - len(unadapted)
    log(f"Converted {len(index)} tasks into {PROCESSED}")
    log(f"Testbenches adapted: {adapted_count}; left unchanged: {len(unadapted)}")
    if unadapted:
        log("Unadapted: " + ", ".join(unadapted))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true", help="Re-download and overwrite processed tasks")
    args = parser.parse_args()
    download_archive()
    convert_all(args.force)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
