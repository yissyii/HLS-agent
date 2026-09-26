"""Public-text slices and prospective family partitions, without an oracle.

Labels locate possible requirements for review. They do not certify a contract
or establish that any generator can implement a task.
"""
from __future__ import annotations

from collections import Counter, defaultdict
import re


FAMILY_SPLITS = {
    "numeric_kernel": "frozen_test",
    "fsm_protocol": "frozen_test",
    "counter": "validation",
    "shift_register": "frozen_test",
    "register": "development",
    "truth_table": "development",
    "bit_permutation": "development",
    "selector_decoder": "frozen_test",
    "arithmetic": "validation",
    "combinational_logic": "frozen_test",
    "unclassified": "quarantine",
}
PILOT_TASK_IDS = frozenset({"Prob004", "Prob039", "Prob067", "Prob070"})
SEP = r"[\s\u2010-\u2015\u2212-]+"
ROLE_EXPRESSIONS = {
    "interface": r"\b(?:prototype|TopModule|interface|ports?)\b",
    "state_reset": (
        r"\b(?:reset\w*|initial(?:ly|ization|isation|ize|ise)?|power" + SEP
        + r"up|active" + SEP + r"(?:high|low))\b"
    ),
    "state_transition": (
        r"\b(?:clocks?|cycles?|edges?|synchronous|asynchronous|rising|falling|"
        r"states?|previous|subsequent|updates?|holds?|retains?|ticks?|enables?|latency)\b"
    ),
    "input_domain": r"\b(?:inputs?|domain|range|legal|valid|assum\w*|constraints?)\b",
    "defined_domain": (
        r"\b(?:don['\u2018\u2019]t" + SEP + r"care|never" + SEP
        + r"occur|undefined|unspecified|invalid|unreachable|independently|"
        r"defined" + SEP + r"domain|only" + SEP + r"when|not" + SEP + r"guaranteed)\b"
    ),
    "output_observation": r"\b(?:outputs?|returns?|produc\w*|generates?|observ\w*|out_\w+)\b",
    "numeric_semantics": (
        r"\b(?:overflow|wrap\w*|signed|unsigned|ap_u?int|ap_u?fixed|truncat\w*|"
        r"round\w*|saturat\w*|modulo|precision|floating" + SEP + r"point|fixed" + SEP + r"point)\b"
    ),
    "bit_layout": r"\b(?:MSB|LSB|bits?|bytes?|endianness|concatenat\w*|revers\w*|indices|index)\b",
    "comparison": r"\b(?:tolerance|epsilon|errors?|equals?|equal(?:ity)?|difference|within|approximately)\b",
    "protocol": r"\b(?:handshake|ready|valid|protocol|serial|hdlc|ps[ /-]?2|uart|packets?|frames?)\b",
}
ROLE_PATTERNS = {name: re.compile(expression, re.I) for name, expression in ROLE_EXPRESSIONS.items()}
_STATE = re.compile(r"\b(?:states?|clocks?|cycles?|flip[\s\u2010-\u2015-]+flops?)\b", re.I)
_LOGIC = re.compile(r"\b(?:boolean|combinational|logic|gates?|xor|xnor|nand|nor)\b", re.I)
_PROTOTYPE = re.compile(r"\b(?:void|int|bool|float|double|ap_\w+\s*<[^>]+>)\s+TopModule\s*\(", re.I)
_SENTENCE_BREAK = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9`\u4e00-\u9fff])")


def _trim_span(text: str, start: int, end: int) -> tuple[int, int]:
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    return start, end


def _paragraphs(text: str):
    """Keep fenced blocks intact even when they contain blank lines."""
    start = None
    fence = None
    offset = 0
    for line in text.splitlines(keepends=True):
        end = offset + len(line)
        marker = re.match(r"[ \t]*(`{3,}|~{3,})", line)
        if fence is not None:
            if marker and marker.group(1)[0] == fence[0] and len(marker.group(1)) >= len(fence):
                yield (*_trim_span(text, start, end), "code")
                start, fence = None, None
        elif marker:
            if start is not None:
                yield (*_trim_span(text, start, offset), None)
            start, fence = offset, marker.group(1)
        elif not line.strip():
            if start is not None:
                yield (*_trim_span(text, start, offset), None)
                start = None
        elif start is None:
            start = offset
        offset = end
    if start is not None:
        yield (*_trim_span(text, start, len(text)), "code" if fence else None)


def _structure(text: str) -> str:
    if _PROTOTYPE.search(text):
        return "prototype"
    lines = [line for line in text.splitlines() if line.strip()]
    if sum("|" in line for line in lines) >= 2:
        return "table"
    if sum(bool(re.fullmatch(r"\s*[\d01xX+-]+(?:[\s,]+[\d01xX+-]+){2,}\s*", line)) for line in lines) >= 2:
        return "table"
    if sum("\t" in line or bool(re.search(r"\S[ ]{2,}\S", line)) for line in lines) >= 2:
        return "table"
    return "prose"


def semantic_slices(problem: str) -> list[dict]:
    if not isinstance(problem, str):
        raise ValueError("problem must be decoded text")
    slices = []
    for context_start, context_end, explicit_structure in _paragraphs(problem):
        context = problem[context_start:context_end]
        structure = explicit_structure or _structure(context)
        bounds = []
        start = context_start
        if structure == "prose":
            for match in _SENTENCE_BREAK.finditer(context):
                bounds.append((start, context_start + match.start()))
                start = context_start + match.end()
        bounds.append((start, context_end))
        for raw_start, raw_end in bounds:
            start, end = _trim_span(problem, raw_start, raw_end)
            if start == end:
                continue
            text = problem[start:end]
            role_matches = {}
            for role, pattern in ROLE_PATTERNS.items():
                hits = [dict(start=start + hit.start(), end=start + hit.end(), text=hit.group())
                        for hit in pattern.finditer(text)]
                if hits:
                    role_matches[role] = hits
            roles = sorted(role_matches) or ["function_definition"]
            slices.append(dict(
                id=f"s{len(slices) + 1:04d}", start=start, end=end, text=text,
                context_start=context_start, context_end=context_end,
                roles=roles, role_matches=role_matches, structure=structure,
                annotation_status="heuristic_unreviewed",
            ))
    return slices


def _source_matches(pattern: re.Pattern, problem: str) -> list[dict]:
    return [dict(start=match.start(), end=match.end(), text=match.group())
            for match in pattern.finditer(problem)]


def _family(row: dict, problem: str) -> tuple[str, list[dict]]:
    tags = row.get("behavior_matches", {})
    basis = lambda tag: [dict(tag=tag, matches=tags[tag])]
    for family in ("numeric_kernel", "fsm_protocol", "counter"):
        if tags.get(family):
            return family, basis(family)
    if tags.get("shift") and (tags.get("register") or tags.get("reset") or _STATE.search(problem)):
        evidence = basis("shift")
        for tag in ("register", "reset"):
            if tags.get(tag):
                evidence.extend(basis(tag))
        if len(evidence) == 1:
            evidence.append(dict(tag="stateful_text", matches=_source_matches(_STATE, problem)))
        return "shift_register", evidence
    for tag, family in (("register", "register"), ("truth_table", "truth_table"),
                        ("bit_permutation", "bit_permutation"), ("selection", "selector_decoder"),
                        ("arithmetic", "arithmetic")):
        if tags.get(tag):
            return family, basis(tag)
    if tags.get("shift"):
        return "bit_permutation", basis("shift")
    logic = _source_matches(_LOGIC, problem)
    if logic:
        return "combinational_logic", [dict(tag="logic_text", matches=logic)]
    return "unclassified", []


def _interface_profile(row: dict, problem: str) -> dict:
    signature = row.get("signature")
    # These are syntax hints, not a second interface parser or support verdict.
    prototypes = [match.group() for match in re.finditer(
        r"\b[A-Za-z_]\w*(?:\s*<[^>]+>)?\s+TopModule\s*\([^;{}]*?\)", problem, re.S)]
    public_interface = "\n".join(prototypes)
    return dict(
        parser_status="parsed_scalar" if signature else "unparsed",
        frontend_support="not_assessed",
        inputs=[item["name"] for item in signature["inputs"]] if signature else [],
        outputs=[item["name"] for item in signature["outputs"]] if signature else [],
        parameter_count=len(signature["params"]) if signature else None,
        syntax_hints={
            "array": bool(re.search(r"\[[^\]]*\]", public_interface)),
            "pointer": "*" in public_interface,
            "floating_point": bool(re.search(r"\b(?:float|double)\b", public_interface)),
            "fixed_point": bool(re.search(r"\bap_u?fixed\b", public_interface)),
            "stream": bool(re.search(r"\b(?:hls\s*::\s*)?stream\b", public_interface)),
        },
    )


def annotate_task(inventory_row: dict, problem: str) -> dict:
    family, family_basis = _family(inventory_row, problem)
    return dict(inventory_row, family=family, split=FAMILY_SPLITS[family],
                family_basis=family_basis, semantic_slices=semantic_slices(problem),
                interface_profile=_interface_profile(inventory_row, problem))


def validate_partition(records: list[dict]) -> dict:
    seen = set()
    family_counts, split_counts = Counter(), Counter()
    duplicate_groups = []
    for row in records:
        task_id, family, split = row["task_id"], row["family"], row["split"]
        if task_id in seen:
            raise ValueError("Duplicate task ID: " + task_id)
        seen.add(task_id)
        if family not in FAMILY_SPLITS or split != FAMILY_SPLITS[family]:
            raise ValueError("Family/split mismatch: " + task_id)
        if task_id in PILOT_TASK_IDS and split != "development":
            raise ValueError("Prior pilot task must stay in development: " + task_id)
        family_counts[family] += 1
        split_counts[split] += 1
    for key in ("normalized_text_sha256", "parameter_normalized_sha256"):
        groups = defaultdict(list)
        for row in records:
            digest = row.get(key)
            if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
                raise ValueError("Missing or invalid duplicate fingerprint: " + row["task_id"])
            groups[digest].append(row)
        for digest, group in sorted(groups.items()):
            if len(group) < 2:
                continue
            if len({row["split"] for row in group}) != 1:
                raise ValueError("Duplicate candidates cross splits: " + ", ".join(row["task_id"] for row in group))
            duplicate_groups.append(dict(kind=key, sha256=digest,
                                         task_ids=sorted(row["task_id"] for row in group), split=group[0]["split"]))
    return dict(task_count=len(records), slice_count=sum(len(row["semantic_slices"]) for row in records),
                split_counts=dict(sorted(split_counts.items())), family_counts=dict(sorted(family_counts.items())),
                duplicate_groups=duplicate_groups)
