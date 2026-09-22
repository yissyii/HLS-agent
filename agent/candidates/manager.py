"""Candidate selection is based on evidence, never the last response alone."""
from dataclasses import asdict
from agent.core.contracts import Candidate, digest
from serve.inference import Failure


class Candidates:
    def __init__(self, task_sha256, config_sha256):
        self.items = []
        self.task_sha256 = task_sha256
        self.config_sha256 = config_sha256

    def add(self, number, source, path, parent_id):
        item = Candidate(number, source, path, parent_id)
        if any(c.sha256 == item.sha256 for c in self.items):
            return None
        self.items.append(item)
        return item

    def contains(self, source):
        return any(c.sha256 == digest(source.encode('utf-8')) for c in self.items)

    def attach(self, candidate, result):
        if (result.source_sha256 != candidate.sha256 or result.task_sha256 != self.task_sha256
                or result.config_sha256 != self.config_sha256):
            raise Failure('evidence_mismatch', 'Validation belongs to different code, task or tool settings')
        candidate.checks.update(result.checks)
        candidate.validations.append(asdict(result))

    def best(self):
        return max(self.items, key=lambda c: (c.rank, -c.candidate_id), default=None)

    def history(self):
        return [dict(candidate_id=c.candidate_id, parent_id=c.parent_id, source_sha256=c.sha256,
                     checks=c.checks, rank=c.rank, validation_history=c.validations) for c in self.items]
