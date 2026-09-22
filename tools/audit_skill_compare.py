#!/usr/bin/env python3
"""Audit RAG-off Workflow Skill evidence for one run or an ablation batch."""

import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent.core.contracts import digest, json_digest


EXPECTED_SKILLS = {
    "S0": [],
    "S1": ["problem-contract"],
    "S2": ["problem-contract", "functional-selftest"],
    "S3": ["problem-contract", "functional-selftest", "synth-guard"],
}


def _read_json(path, errors, label):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError) as error:
        errors.append(f"{label}: {type(error).__name__}: {error}")
        return None


def _events(path, errors):
    try:
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    except (OSError, ValueError, TypeError) as error:
        errors.append(f"events.jsonl: {type(error).__name__}: {error}")
        return []


def audit_run(directory, condition=None, expected_policy=None):
    directory = Path(directory).resolve()
    errors = []
    receipt = _read_json(directory / "result.json", errors, "result.json")
    if not isinstance(receipt, dict):
        return errors or ["result.json is not an object"]
    if receipt.get("rag_enabled") is not False:
        errors.append("receipt does not prove rag_enabled=false")
    if receipt.get("rag_history") != []:
        errors.append("receipt has nonempty or missing rag_history")
    if any(directory.rglob("retrieval*.json")):
        errors.append("retrieval artifact exists in a RAG-off run")
    for prompt in directory.rglob("prompt.txt"):
        try:
            if "<REFERENCE_MATERIAL>" in prompt.read_text(encoding="utf-8"):
                errors.append("RAG reference material appears in " + prompt.relative_to(directory).as_posix())
        except (OSError, UnicodeError) as error:
            errors.append(f"cannot inspect {prompt.name}: {error}")

    policy = _read_json(directory / "policy.json", errors, "policy.json")
    if isinstance(policy, dict):
        if policy.get("rag_enabled") is not False or policy.get("skills_enabled") is not False:
            errors.append("policy enables RAG or legacy repair rules")
        if expected_policy is not None and policy != expected_policy:
            errors.append("run policy differs from frozen condition policy")
        if receipt.get("policy_sha256") != json_digest(policy):
            errors.append("policy_sha256 mismatch")

    snapshot = _read_json(directory / "workflow_skills.json", errors, "workflow_skills.json")
    if isinstance(snapshot, dict):
        if receipt.get("workflow_skills_sha256") != json_digest(snapshot):
            errors.append("workflow_skills_sha256 mismatch")
        if condition in EXPECTED_SKILLS and snapshot.get("enabled") != EXPECTED_SKILLS[condition]:
            errors.append(f"{condition} enabled Skill list mismatch")
        if receipt.get("workflow_skills_enabled") != snapshot.get("enabled"):
            errors.append("receipt Workflow Skill list differs from snapshot")

    events = _events(directory / "events.jsonl", errors)
    candidate_positions = [i for i, event in enumerate(events) if event.get("event") == "candidate_created"]
    first_candidate = min(candidate_positions) if candidate_positions else None
    enabled = snapshot.get("enabled", []) if isinstance(snapshot, dict) else []
    ready_positions = {}
    for i, event in enumerate(events):
        if event.get("event") == "workflow_artifact_ready":
            ready_positions[event.get("skill")] = i
    for skill in ("problem-contract", "functional-selftest"):
        if skill in enabled:
            if skill not in ready_positions:
                if first_candidate is not None or not receipt.get("category") or not receipt.get("workflow_requests"):
                    errors.append(skill + " ready event is missing without an explicit pre-candidate failure")
            elif first_candidate is not None and ready_positions[skill] >= first_candidate:
                errors.append(skill + " evidence was not frozen before candidate 0")

    contract = plan = None
    if "problem-contract" in ready_positions:
        contract = _read_json(directory / "workflow/problem-contract/contract.json", errors, "contract.json")
        plan = _read_json(directory / "workflow/problem-contract/test_plan.json", errors, "test_plan.json")
        if isinstance(contract, dict) and contract.get("task_sha256") != receipt.get("task_sha256"):
            errors.append("contract task_sha256 mismatch")
        if isinstance(plan, dict):
            if plan.get("task_sha256") != receipt.get("task_sha256"):
                errors.append("test plan task_sha256 mismatch")
            if isinstance(contract, dict) and plan.get("contract_sha256") != json_digest(contract):
                errors.append("test plan contract_sha256 mismatch")

    bundle = None
    if "functional-selftest" in ready_positions:
        bundle = _read_json(directory / "workflow/functional-selftest/bundle.json", errors, "self-test bundle")
        testbench = directory / "workflow/functional-selftest/testbench.cpp"
        if isinstance(bundle, dict):
            unsigned = {key: value for key, value in bundle.items() if key != "bundle_sha256"}
            if bundle.get("bundle_sha256") != json_digest(unsigned):
                errors.append("self-test bundle_sha256 mismatch")
            if bundle.get("task_sha256") != receipt.get("task_sha256"):
                errors.append("self-test bundle task_sha256 mismatch")
            if isinstance(contract, dict) and bundle.get("contract_sha256") != json_digest(contract):
                errors.append("self-test bundle contract_sha256 mismatch")
            if isinstance(plan, dict) and bundle.get("test_plan_sha256") != json_digest(plan):
                errors.append("self-test bundle test_plan_sha256 mismatch")
            try:
                if bundle.get("testbench_sha256") != digest(testbench.read_bytes()):
                    errors.append("self-test testbench_sha256 mismatch")
            except OSError as error:
                errors.append("self-test testbench missing: " + str(error))

    config = _read_json(directory / "config.json", errors, "config.json")
    hls_sha = json_digest(config["hls"]) if isinstance(config, dict) and isinstance(config.get("hls"), dict) else None
    candidate_hashes = {}
    for candidate in receipt.get("candidates", []):
        if isinstance(candidate, dict):
            candidate_hashes[candidate.get("candidate_id")] = candidate.get("source_sha256")
    for candidate_dir in sorted((directory / "candidates").glob("[0-9][0-9][0-9]")) if (directory / "candidates").is_dir() else []:
        attempt = int(candidate_dir.name)
        source = candidate_dir / "candidate.cpp"
        if source.is_file() and candidate_hashes.get(attempt) != digest(source.read_bytes()):
            errors.append(f"candidate {attempt} source hash mismatch")
        selftest = candidate_dir / "selftest.json"
        if selftest.is_file():
            result = _read_json(selftest, errors, f"candidate {attempt} selftest")
            if isinstance(result, dict):
                if result.get("source_sha256") != candidate_hashes.get(attempt):
                    errors.append(f"candidate {attempt} self-test source hash mismatch")
                if isinstance(bundle, dict) and result.get("bundle_sha256") != bundle.get("bundle_sha256"):
                    errors.append(f"candidate {attempt} self-test bundle hash mismatch")
                if hls_sha and result.get("config_sha256") != hls_sha:
                    errors.append(f"candidate {attempt} self-test config hash mismatch")
        guard_path = candidate_dir / "synth_guard.json"
        if guard_path.is_file():
            guard = _read_json(guard_path, errors, f"candidate {attempt} synth guard")
            if isinstance(guard, dict) and guard.get("source_sha256") != candidate_hashes.get(attempt):
                errors.append(f"candidate {attempt} synth-guard source hash mismatch")

    for i, event in enumerate(events):
        if event.get("event") == "validation_started" and event.get("stage") == "synthesis" and "synth-guard" in enabled:
            attempt = event.get("attempt")
            earlier = [prior for prior in events[:i] if prior.get("event") == "synth_guard_finished"
                       and prior.get("attempt") == attempt]
            if not earlier:
                errors.append(f"candidate {attempt} synthesis started before synth-guard evidence")
    return errors


def audit_batch(batch):
    batch = Path(batch).resolve()
    errors = []
    summary = _read_json(batch / "summary.json", errors, "summary.json")
    if not isinstance(summary, dict):
        return errors
    for name, expected in (summary.get("frozen_snapshot_sha256") or {}).items():
        path = Path(name)
        try:
            actual = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError as error:
            errors.append("frozen input missing: " + str(error))
            continue
        if actual != expected:
            errors.append("frozen input changed: " + str(path))
    policies = summary.get("policies") or {}
    for row in summary.get("results", []):
        directory = Path(row.get("directory", ""))
        condition = row.get("condition")
        policy = policies.get(condition)
        run_errors = audit_run(directory, condition, policy)
        errors.extend(f"{row.get('task')}/{condition}: {message}" for message in run_errors)
    return errors


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", help="Agent run directory or Skill ablation batch directory")
    parser.add_argument("--condition", choices=tuple(EXPECTED_SKILLS))
    args = parser.parse_args()
    path = Path(args.path)
    errors = audit_batch(path) if (path / "summary.json").is_file() else audit_run(path, args.condition)
    print(json.dumps({"status": "passed" if not errors else "failed", "errors": errors},
                     ensure_ascii=False, indent=2))
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
