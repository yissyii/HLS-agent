"""Shared value types. No orchestration or tool dependencies."""
from dataclasses import dataclass, field
from pathlib import Path
import hashlib
import json
import time
from typing import Protocol


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def json_digest(value) -> str:
    # Same byte representation as serve.baseline_entry.config_digest.
    return digest(json.dumps(value, sort_keys=True, separators=(',', ':')).encode())


def empty_checks():
    return dict.fromkeys(('parse', 'compile', 'run', 'synthesize'), 'not_run')


@dataclass(frozen=True)
class Material:
    name: str
    content: bytes
    model_visible: bool = False


@dataclass
class TaskSpec:
    problem: bytes
    manifest: dict | None = None
    materials: list[Material] = field(default_factory=list)

    @property
    def fingerprint(self):
        return json_digest(self.snapshot())

    def snapshot(self):
        return {'problem_sha256': digest(self.problem), 'manifest': self.manifest,
                'materials': [{'name': m.name, 'sha256': digest(m.content),
                               'model_visible': m.model_visible} for m in self.materials]}

    @property
    def stages(self):
        if self.manifest is None:
            return ()
        return ('csim', 'synthesis') if self.manifest['testbench_files'] else ('synthesis',)


@dataclass
class Candidate:
    candidate_id: int
    source: str
    path: Path
    parent_id: int | None
    checks: dict = field(default_factory=empty_checks)
    validations: list = field(default_factory=list)

    @property
    def sha256(self):
        return digest(self.source.encode('utf-8'))

    @property
    def rank(self):
        if self.checks['run'] == 'passed':
            return 4 if self.checks['synthesize'] == 'passed' else 3
        if self.checks['compile'] == 'passed':
            return 2
        return 1 if self.checks['synthesize'] == 'passed' else 0


@dataclass
class ValidationResult:
    source_sha256: str
    task_sha256: str
    config_sha256: str
    stage: str
    outcome: dict
    checks: dict
    feedback_text: str
    validator_version: str = 'vitis-adapter-v1'


@dataclass
class Diagnostic:
    category: str
    stage: str
    feedback: str
    fingerprint: str
    repairable: bool
    location: str | None = None


@dataclass
class PromptBundle:
    text: str
    provenance: list[dict]
    context: dict
    skills: list[str] = field(default_factory=list)


@dataclass
class Budget:
    started: float
    deadline: float
    requests: int = 0
    tool_calls: int = 0

    def remaining(self):
        return max(0.0, self.deadline - time.monotonic())


class Model(Protocol):
    def generate(self, prompt: PromptBundle, runtime: dict, directory: Path, deadline: float) -> dict: ...


class Validator(Protocol):
    def preflight(self, task: TaskSpec, runtime: dict, directory: Path) -> None: ...
    def check(self, candidate: Candidate, task: TaskSpec, runtime: dict,
              stage: str, deadline: float) -> ValidationResult: ...
