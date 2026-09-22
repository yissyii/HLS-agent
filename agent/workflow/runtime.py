"""Frozen workflow-skill packages and their typed runtime artifacts."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import re

from agent.core.contracts import Material, PromptBundle, TaskSpec, digest, json_digest
from agent.core.policy import workflow_options
from serve.code import extract_code
from serve.inference import Failure, ROOT


SKILL_ROOT = ROOT / "skill"
PACKAGE_FILES = {
    "problem-contract": (
        "problem-contract/SKILL.md",
        "problem-contract/references/artifacts.md",
        "problem-contract/templates/system.txt",
        "problem-contract/templates/user.txt",
    ),
    "functional-selftest": (
        "functional-selftest/SKILL.md",
        "functional-selftest/templates/system.txt",
        "functional-selftest/templates/user.txt",
    ),
    "synth-guard": (
        "synth-guard/SKILL.md",
        "synth-guard/scripts/synth_guard.py",
    ),
}


def enabled_names(policy):
    options = workflow_options(policy)
    names = []
    if options["problem_contract_enabled"]:
        names.append("problem-contract")
    if options["functional_selftest_enabled"]:
        names.append("functional-selftest")
    if options["synth_guard_enabled"]:
        names.append("synth-guard")
    return names


def input_paths(policy, root=None):
    root = Path(root).resolve() if root is not None else SKILL_ROOT
    return [root / name for skill in enabled_names(policy) for name in PACKAGE_FILES[skill]]


def _strict_json(text):
    value = text.strip()
    if value.startswith("```"):
        lines = value.splitlines()
        if len(lines) >= 3 and lines[-1].strip().startswith("```"):
            value = "\n".join(lines[1:-1]).strip()
    try:
        result = json.loads(value)
    except (ValueError, TypeError) as error:
        raise Failure("workflow_output_error", "Workflow model response is not one JSON object") from error
    if not isinstance(result, dict):
        raise Failure("workflow_output_error", "Workflow model response must be a JSON object")
    return result


class WorkflowSkills:
    def __init__(self, policy, root=None):
        self.options = workflow_options(policy)
        self.enabled = enabled_names(policy)
        self.root = Path(root).resolve() if root is not None else SKILL_ROOT
        self.files = {}
        for path in input_paths(policy, self.root):
            try:
                raw = path.read_bytes()
            except OSError as error:
                raise Failure("workflow_configuration_error", "Cannot read workflow skill resource: " + str(path)) from error
            if not raw:
                raise Failure("workflow_configuration_error", "Empty workflow skill resource: " + str(path))
            self.files[path.relative_to(self.root).as_posix()] = {
                "sha256": digest(raw),
                "bytes": len(raw),
            }
        self.sha256 = json_digest(self.snapshot())
        self._scanner = None

    def snapshot(self):
        return {
            "schema_version": 1,
            "enabled": list(self.enabled),
            "options": self.options,
            "files": self.files,
        }

    def _text(self, relative):
        path = self.root / relative
        try:
            return path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as error:
            raise Failure("workflow_configuration_error", "Cannot read workflow text: " + str(path)) from error

    def _prompt(self, system, user, runtime, policy, provenance, stage):
        available = (runtime["model"]["context_tokens"] - runtime["model"]["max_tokens"]
                     - policy["context_safety_tokens"])
        input_bytes = len(system.encode("utf-8")) + len(user.encode("utf-8"))
        overhead = 128
        if input_bytes + overhead > available:
            raise Failure("context_budget_exceeded", stage + " workflow prompt exceeds conservative budget")
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        return PromptBundle(
            user,
            provenance,
            {
                "method": "conservative_utf8_bytes",
                "token_count_verified": False,
                "input_bytes": input_bytes,
                "input_budget": available,
                "message_overhead_bytes": overhead,
                "budgeted_input_bytes": input_bytes + overhead,
                "stage": stage,
                "messages_sha256": json_digest(messages),
                "output_tokens": runtime["model"]["max_tokens"],
                "safety_tokens": policy["context_safety_tokens"],
            },
            [stage],
            system=system,
        )

    def _public_problem(self, task):
        problem = task.problem.decode("utf-8")
        text = "<PROBLEM>\n" + problem + "\n</PROBLEM>\n"
        provenance = [{"kind": "problem", "sha256": digest(task.problem)}]
        if task.manifest:
            text += "<TOP_FUNCTION>" + task.manifest["top_function"] + "</TOP_FUNCTION>\n"
        for material in task.materials:
            if material.model_visible:
                text += f'<PUBLIC_FILE name="{material.name}">\n{material.content.decode("utf-8")}\n</PUBLIC_FILE>\n'
                provenance.append({"kind": "public_file", "name": material.name,
                                   "sha256": digest(material.content)})
        return text, provenance

    def contract_prompt(self, task, runtime, policy):
        if "problem-contract" not in self.enabled:
            raise Failure("workflow_configuration_error", "problem-contract is not enabled")
        public, provenance = self._public_problem(task)
        system = self._text("problem-contract/templates/system.txt")
        user = self._text("problem-contract/templates/user.txt") + "\n" + public
        provenance += [
            {"kind": "workflow_skill", "id": "problem-contract", "sha256": self.sha256},
        ]
        return self._prompt(system, user, runtime, policy, provenance, "problem-contract")

    def parse_contract(self, response, task, prompt):
        value = _strict_json(response)
        if set(value) != {"schema_version", "contract", "test_plan"} or value["schema_version"] != 1:
            raise Failure("workflow_output_error", "Unexpected problem-contract response fields")
        contract, plan = value["contract"], value["test_plan"]
        if not isinstance(contract, dict) or not isinstance(plan, dict):
            raise Failure("workflow_output_error", "Contract and test_plan must be objects")
        top = task.manifest["top_function"] if task.manifest else None
        required_contract = {"schema_version", "status", "top_function", "interface", "behavior", "state",
                             "numeric", "boundaries", "performance_constraints", "risk_points", "assumptions", "unresolved"}
        required_plan = {"schema_version", "contract_status", "oracle_strategy", "cases", "properties",
                         "random", "tolerance", "unresolved"}
        if not required_contract.issubset(contract) or not required_plan.issubset(plan):
            raise Failure("workflow_output_error", "Contract or test_plan is missing required fields")
        if contract["schema_version"] != 1 or plan["schema_version"] != 1:
            raise Failure("workflow_output_error", "Unsupported workflow artifact schema")
        if contract["status"] not in {"ready", "needs_clarification"} or plan["contract_status"] != contract["status"]:
            raise Failure("workflow_output_error", "Inconsistent contract status")
        if top and contract["top_function"] != top:
            raise Failure("workflow_output_error", "Contract top function differs from task")
        if not isinstance(contract["behavior"], list) or not isinstance(plan["cases"], list):
            raise Failure("workflow_output_error", "Behavior and cases must be lists")
        if contract["status"] == "ready" and (not contract["behavior"] or not plan["cases"]):
            raise Failure("workflow_output_error", "Ready contract and test plan must be nonempty")
        if contract["status"] != "ready":
            raise Failure("problem_contract_ambiguous", "Problem contract requires clarification")
        binding = {
            "task_sha256": task.fingerprint,
            "problem_sha256": digest(task.problem),
            "request_sha256": prompt.context["messages_sha256"],
            "created_before_candidate": True,
        }
        contract = {**contract, **binding}
        contract_sha = json_digest(contract)
        plan = {**plan, **binding, "contract_sha256": contract_sha}
        limit = self.options["workflow_context_max_bytes"]
        size = len(json.dumps({"contract": contract, "test_plan": plan}, ensure_ascii=False).encode("utf-8"))
        if size > limit:
            raise Failure("workflow_output_error", "Contract artifacts exceed workflow_context_max_bytes")
        return contract, plan

    def selftest_prompt(self, task, runtime, policy, contract, plan):
        if "functional-selftest" not in self.enabled:
            raise Failure("workflow_configuration_error", "functional-selftest is not enabled")
        if contract.get("task_sha256") != task.fingerprint or plan.get("task_sha256") != task.fingerprint:
            raise Failure("evidence_mismatch", "Self-test inputs belong to a different task")
        if plan.get("contract_sha256") != json_digest(contract):
            raise Failure("evidence_mismatch", "Self-test plan does not match the contract")
        public, provenance = self._public_problem(task)
        system = self._text("functional-selftest/templates/system.txt")
        user = (self._text("functional-selftest/templates/user.txt") + "\n" + public
                + "<BEHAVIOR_CONTRACT>\n" + json.dumps(contract, ensure_ascii=False, sort_keys=True)
                + "\n</BEHAVIOR_CONTRACT>\n<TEST_PLAN>\n"
                + json.dumps(plan, ensure_ascii=False, sort_keys=True) + "\n</TEST_PLAN>\n")
        provenance += [
            {"kind": "workflow_skill", "id": "functional-selftest", "sha256": self.sha256},
            {"kind": "behavior_contract", "sha256": json_digest(contract)},
            {"kind": "test_plan", "sha256": json_digest(plan)},
        ]
        return self._prompt(system, user, runtime, policy, provenance, "functional-selftest")

    def parse_selftest(self, response, task, contract, plan, prompt):
        details = {}
        source, extraction = extract_code(response, top_function="main", details=details)
        selected = next((item for item in details.get("candidates", [])
                         if item.get("index") == details.get("selected_block")), None)
        target_defined = selected.get("target_defined") if selected else bool(re.search(r"\bmain\s*\(", source))
        if not target_defined:
            raise Failure("selftest_invalid", "Generated self-test does not define main()")
        top = task.manifest["top_function"] if task.manifest else None
        main_start = re.search(r"\bmain\s*\([^)]*\)\s*\{", source)
        if top and (main_start is None or not re.search(r"\b" + re.escape(top) + r"\s*\(",
                                                       source[main_start.end():])):
            raise Failure("selftest_invalid", "Generated self-test main() does not directly exercise the top function")
        bundle = {
            "schema_version": 1,
            "runner_version": "generated-selftest-v1",
            "task_sha256": task.fingerprint,
            "contract_sha256": json_digest(contract),
            "test_plan_sha256": json_digest(plan),
            "request_sha256": prompt.context["messages_sha256"],
            "created_before_candidate": True,
            "testbench_sha256": digest(source.encode("utf-8")),
            "extraction": extraction,
        }
        bundle["bundle_sha256"] = json_digest(bundle)
        return source, bundle, details

    def selftest_task(self, task, source, bundle):
        if not task.manifest:
            raise Failure("selftest_unavailable", "Functional self-test requires a task manifest")
        if bundle.get("task_sha256") != task.fingerprint or bundle.get("testbench_sha256") != digest(source.encode("utf-8")):
            raise Failure("evidence_mismatch", "Self-test bundle is stale")
        name = "__zcomp_skill_selftest.cpp"
        existing = {material.name.casefold() for material in task.materials}
        while name.casefold() in existing:
            name = "_" + name
        original_tests = set(task.manifest["testbench_files"])
        materials = [material for material in task.materials if material.name not in original_tests]
        materials.append(Material(name, source.encode("utf-8"), False))
        manifest = dict(task.manifest)
        manifest.update(id=task.manifest["id"] + "-skill-selftest",
                        testbench_files=[name], feedback_policy="functional_diagnostics",
                        model_visible=[])
        return TaskSpec(task.problem, manifest, materials), name

    def scan(self, source, source_name):
        if "synth-guard" not in self.enabled:
            raise Failure("workflow_configuration_error", "synth-guard is not enabled")
        if self._scanner is None:
            path = self.root / "synth-guard/scripts/synth_guard.py"
            spec = importlib.util.spec_from_file_location("zcomp_workflow_synth_guard", path)
            if spec is None or spec.loader is None:
                raise Failure("workflow_configuration_error", "Cannot load synth-guard scanner")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            self._scanner = module
        result = self._scanner.scan_text(source, source_name)
        if result.get("source_sha256") != digest(source.encode("utf-8")):
            raise Failure("evidence_mismatch", "Synth-guard result belongs to different source")
        return result
