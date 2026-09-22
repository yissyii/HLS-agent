#!/usr/bin/env python3
"""Deterministic, conservative source scan for HLS synthesis risks.

This scanner reports lexical evidence. It does not parse C++, determine the
synthesized call graph, or replace a Vitis synthesis result.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys


SCHEMA_VERSION = 1
SCANNER_VERSION = "synth-guard-v1"
SEVERITY_RANK = {"info": 1, "warning": 2, "error": 3}


RULES = (
    (
        "dynamic-allocation",
        "error",
        re.compile(r"\b(?:malloc|calloc|realloc|free)\s*\(|\b(?:new|delete)\b|\b(?:std\s*::\s*)?make_(?:unique|shared)\s*<"),
        "Dynamic allocation or deallocation requires synthesis-support review.",
    ),
    (
        "dynamic-stl-container",
        "warning",
        re.compile(r"\b(?:std\s*::\s*)?(?:vector|map|unordered_map|set|unordered_set|list|deque|string)\s*<|\bstd\s*::\s*string\b"),
        "A dynamic standard-library container appears in candidate code.",
    ),
    (
        "host-file-io",
        "error",
        re.compile(r"\b(?:fopen|freopen|fclose|fread|fwrite|ifstream|ofstream|fstream)\b|\bFILE\s*\*"),
        "Host file I/O appears in candidate code.",
    ),
    (
        "host-process-call",
        "error",
        re.compile(r"\b(?:system|popen|_popen)\s*\("),
        "A host process or shell call appears in candidate code.",
    ),
    (
        "exception-control-flow",
        "warning",
        re.compile(r"\b(?:try|catch|throw)\b"),
        "Exception-based control flow requires synthesis-support review.",
    ),
    (
        "potentially-unbounded-loop",
        "warning",
        re.compile(r"\bwhile\s*\(|\bfor\s*\(\s*;\s*;"),
        "A loop lacks a statically evident for-loop bound.",
    ),
    (
        "floating-point-operation",
        "info",
        re.compile(r"\b(?:float|double|long\s+double)\b"),
        "Floating-point code may affect latency and resource use.",
    ),
)

FUNCTION_DEFINITION = re.compile(
    r"\b([A-Za-z_]\w*)\s*\([^;{}]*\)\s*(?:const\s*)?(?:noexcept\s*)?\{"
)
CONTROL_NAMES = {"if", "for", "while", "switch", "catch"}
FOR_CONDITION = re.compile(r"\bfor\s*\(([^;]*);([^;]+);[^)]*\)")
LOOP_INITIALIZER = re.compile(r"\b([A-Za-z_]\w*)\s*=")
COMPARISON = re.compile(r"([A-Za-z_]\w*|-?\d+[uUlL]*)\s*(?:<=?|>=?)\s*([A-Za-z_]\w*|-?\d+[uUlL]*)")
INTEGER_LITERAL = re.compile(r"-?\d+[uUlL]*")


def mask_comments_and_literals(text: str) -> str:
    """Replace comments and quoted literal contents while preserving newlines."""
    result = list(text)
    i = 0
    state = "code"
    quote = ""
    while i < len(text):
        char = text[i]
        nxt = text[i + 1] if i + 1 < len(text) else ""
        if state == "code":
            if char == "/" and nxt == "/":
                result[i] = result[i + 1] = " "
                i += 2
                state = "line_comment"
                continue
            if char == "/" and nxt == "*":
                result[i] = result[i + 1] = " "
                i += 2
                state = "block_comment"
                continue
            if char == "R" and nxt == '"':
                opening = re.match(r'R"([^ ()\\\t\r\n]{0,16})\(', text[i:])
                if opening:
                    closing_marker = ")" + opening.group(1) + '"'
                    closing = text.find(closing_marker, i + opening.end())
                    end = len(text) if closing < 0 else closing + len(closing_marker)
                    for position in range(i, end):
                        if text[position] != "\n":
                            result[position] = " "
                    i = end
                    continue
            if char in {'"', "'"}:
                quote = char
                result[i] = " "
                i += 1
                state = "literal"
                continue
        elif state == "line_comment":
            if char == "\n":
                slash_count = 0
                position = i - 1
                while position >= 0 and text[position] == "\\":
                    slash_count += 1
                    position -= 1
                if slash_count % 2 == 0:
                    state = "code"
            else:
                result[i] = " "
            i += 1
            continue
        elif state == "block_comment":
            if char == "*" and nxt == "/":
                result[i] = result[i + 1] = " "
                i += 2
                state = "code"
                continue
            if char != "\n":
                result[i] = " "
            i += 1
            continue
        elif state == "literal":
            if char == "\\":
                result[i] = " "
                if i + 1 < len(text):
                    if text[i + 1] != "\n":
                        result[i + 1] = " "
                    i += 2
                    continue
            if char == quote:
                result[i] = " "
                i += 1
                state = "code"
                continue
            if char != "\n":
                result[i] = " "
            i += 1
            continue
        i += 1
    return "".join(result)


def scan_text(text: str, source_name: str = "<memory>") -> dict:
    masked = mask_comments_and_literals(text)
    original_lines = text.splitlines()
    findings = []
    seen = set()
    for rule_id, severity, pattern, message in RULES:
        for match in pattern.finditer(masked):
            line = masked.count("\n", 0, match.start()) + 1
            key = (rule_id, line)
            if key in seen:
                continue
            seen.add(key)
            excerpt = original_lines[line - 1].strip() if line <= len(original_lines) else ""
            findings.append(
                {
                    "rule_id": rule_id,
                    "severity": severity,
                    "line": line,
                    "message": message,
                    "evidence": excerpt[:240],
                }
            )
    for match in FOR_CONDITION.finditer(masked):
        variables = LOOP_INITIALIZER.findall(match.group(1))
        condition = match.group(2)
        comparisons = COMPARISON.findall(condition)
        nonliteral = any(
            left in variables and not INTEGER_LITERAL.fullmatch(right)
            or right in variables and not INTEGER_LITERAL.fullmatch(left)
            for left, right in comparisons
        )
        if nonliteral:
            line = masked.count("\n", 0, match.start()) + 1
            key = ("nonliteral-for-bound", line)
            if key not in seen:
                seen.add(key)
                findings.append({
                    "rule_id": "nonliteral-for-bound",
                    "severity": "info",
                    "line": line,
                    "message": "A for-loop bound is not an obvious numeric literal and may need a declared maximum.",
                    "evidence": original_lines[line - 1].strip()[:240],
                })
    for match in FUNCTION_DEFINITION.finditer(masked):
        name = match.group(1)
        if name in CONTROL_NAMES:
            continue
        opening = match.end() - 1
        depth = 0
        closing = None
        for position in range(opening, len(masked)):
            if masked[position] == "{":
                depth += 1
            elif masked[position] == "}":
                depth -= 1
                if depth == 0:
                    closing = position
                    break
        if closing is None:
            continue
        call = re.search(r"\b" + re.escape(name) + r"\s*\(", masked[opening + 1:closing])
        if call:
            absolute = opening + 1 + call.start()
            line = masked.count("\n", 0, absolute) + 1
            key = ("direct-recursion", line)
            if key not in seen:
                seen.add(key)
                findings.append({
                    "rule_id": "direct-recursion",
                    "severity": "error",
                    "line": line,
                    "message": "Direct recursion appears in a function body and requires synthesis-support review.",
                    "evidence": original_lines[line - 1].strip()[:240],
                })
    findings.sort(key=lambda item: (item["line"], -SEVERITY_RANK[item["severity"]], item["rule_id"]))
    return {
        "schema_version": SCHEMA_VERSION,
        "scanner_version": SCANNER_VERSION,
        "source": source_name,
        "source_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "status": "review_required" if findings else "clear",
        "finding_count": len(findings),
        "findings": findings,
        "limitations": [
            "Lexical scan only; inactive code and synthesized call graph are not resolved.",
            "No finding is not proof of synthesizability; Vitis remains authoritative.",
        ],
    }


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="UTF-8 C/C++ candidate source")
    parser.add_argument("--pretty", action="store_true", help="indent JSON output")
    parser.add_argument(
        "--fail-on",
        choices=("never", "info", "warning", "error"),
        default="never",
        help="return exit code 2 when a finding reaches this threshold",
    )
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    try:
        raw = args.source.read_bytes()
        text = raw.decode("utf-8")
    except (OSError, UnicodeDecodeError) as error:
        print(json.dumps({"status": "input_error", "message": str(error)}, ensure_ascii=False))
        return 1
    result = scan_text(text, args.source.as_posix())
    print(json.dumps(result, ensure_ascii=False, indent=2 if args.pretty else None, sort_keys=True))
    if args.fail_on != "never":
        threshold = SEVERITY_RANK[args.fail_on]
        if any(SEVERITY_RANK[item["severity"]] >= threshold for item in result["findings"]):
            return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
