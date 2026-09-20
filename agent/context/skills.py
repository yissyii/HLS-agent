"""Optional, immutable rule retrieval. No online search or case learning."""
import json
from pathlib import Path

from agent.core.contracts import digest, json_digest
from agent.toolchain import VITIS_VERSION
from serve.inference import Failure, ROOT


class Skills:
    def __init__(self, enabled=False, directory=None):
        self.rules = []
        self.enabled = enabled
        if enabled:
            root = Path(directory or ROOT / 'skill/rules').resolve()
            if not root.is_dir():
                raise Failure('skill_error', 'Enabled skill directory does not exist')
            for path in sorted(root.glob('*.json')):
                if not path.resolve().is_relative_to(root):
                    raise Failure('skill_error', 'Skill must remain within its frozen directory')
                raw = path.read_bytes()
                rule = json.loads(raw.decode('utf-8'))
                required = {'id', 'version', 'tool_version', 'categories', 'keywords', 'guidance',
                            'preconditions', 'failure_modes', 'source', 'license', 'validation'}
                if not required.issubset(rule):
                    raise Failure('skill_error', 'Missing rule provenance or applicability fields')
                if (not isinstance(rule['id'], str) or not rule['id'] or
                        not isinstance(rule['guidance'], str) or not rule['guidance']):
                    raise Failure('skill_error', 'Invalid skill text')
                if any(not isinstance(rule[k], list) or any(not isinstance(x, str) for x in rule[k])
                       for k in ('categories', 'keywords')):
                    raise Failure('skill_error', 'Rule categories and keywords must be string lists')
                if not isinstance(rule['validation'], dict) or rule['validation'].get('held_out_passed') is not True:
                    raise Failure('skill_error', 'Only independently validated rules may be enabled')
                if rule['validation'].get('split') != 'independent_development':
                    raise Failure('skill_error', 'Rule provenance must exclude frozen evaluation data')
                if rule['tool_version'] != VITIS_VERSION:
                    raise Failure('skill_error', 'Rule tool version does not match Vitis ' + VITIS_VERSION)
                rule['sha256'] = digest(raw)
                # Preconditions remain visible to the model; keywords alone do not prove them.
                rule['guidance'] = ('Apply only if: ' + str(rule['preconditions']) + '\n'
                                    + rule['guidance'] + '\nFailure modes: ' + str(rule['failure_modes']))
                self.rules.append(rule)
            if len({r['id'] for r in self.rules}) != len(self.rules):
                raise Failure('skill_error', 'Duplicate skill identifiers')
            if not self.rules:
                raise Failure('skill_error', 'Enabled skill pack has no validated JSON rules')
        self.sha256 = json_digest([{'id': r['id'], 'sha256': r['sha256']} for r in self.rules])

    def select(self, diagnostic, limit):
        if diagnostic is None:
            return []
        text = diagnostic.feedback.lower()
        matches = [r for r in self.rules if diagnostic.category in r['categories']
                   and (not r['keywords'] or any(k.lower() in text for k in r['keywords']))]
        return matches[:limit]
