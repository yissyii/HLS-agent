---
name: synth-guard
description: Run a cheap deterministic scan before Vitis synthesis to surface C/C++ constructs that require synthesis-support review without declaring them unsupported from heuristics alone.
---

# Synthesis Guard

## Trigger

Use on a complete candidate before an expensive synthesis run and after a repair that changes control flow, memory use, C++ library use, exceptions, or host I/O. Do not use it as a substitute for Vitis synthesis.

## Procedure

1. Run `python scripts/synth_guard.py <candidate.cpp> --pretty`.
2. Review each finding in source context. Findings inside inactive code, test-only helpers, or non-synthesizable host wrappers may be inapplicable.
3. For version-dependent support questions, request compact official evidence using the retrieval contract below.
4. If a finding is applicable, give the repair model its rule ID, line, evidence, and a narrow remediation scope.
5. Proceed to real synthesis when no applicable high-risk finding remains. The real tool result is authoritative.

The scanner exits nonzero only for invalid invocation or when an explicit `--fail-on` threshold is requested. Its default `review_required` status is not proof that synthesis will fail.

## Retrieval contract

Use only the official `docs` namespace for construct-support questions, plus validated `failures` when a matching tool signature exists. Return at most three compact evidence items and preserve source type (`official` or `empirical`). Do not retrieve generic algorithm tutorials.

## Output and acceptance

The JSON output records the source hash, scanner version, deterministic findings, and overall status. `clear` means no configured pattern was found, not that the code is synthesizable. Acceptance requires either no applicable finding or a documented reason that each finding is safe, followed by actual Vitis validation.

## Failure modes

- Treating regex evidence as a compiler verdict.
- Rejecting a construct without checking scope and tool version.
- Suppressing findings merely to reach synthesis.
- Adding static facts about changing Vitis support to this Skill.

## Validation status

The scanner has local unit tests for deterministic detection and comment/string suppression. It is integrated in advisory `observe` mode and never blocks authoritative Vitis synthesis. Agent-level precision and pass-rate benefit remain unvalidated, so this Skill is not enabled by default.
