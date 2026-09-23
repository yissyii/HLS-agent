"""Route released diagnostics to frozen, inspectable instruction fragments.

This module never opens validation artifacts or invokes a model/retriever.
The caller remains responsible for feedback visibility and context budgeting.
"""
from dataclasses import dataclass
from pathlib import Path
import re

from agent.core.contracts import Diagnostic, digest, json_digest

VERSION = '0.1.2'
TEMPLATE_ROOT = Path(__file__).with_name('templates')
ROUTES = {
    'compile_error': 'compile',
    'functional_or_runtime_error': 'functional',
    'synthesis_error': 'synthesis',
    'compile_or_csim_error': 'insufficient_evidence',
}
TEMPLATE_NAMES = ('common', 'compile', 'functional', 'synthesis', 'insufficient_evidence')


@dataclass(frozen=True)
class StrategySelection:
    strategy_id: str | None
    reason: str
    category: str
    stage: str
    version: str
    pack_sha256: str
    instruction: str

    @property
    def instruction_sha256(self):
        return digest(self.instruction.encode('utf-8'))

    def render(self):
        """Return a fragment, not a complete prompt or permission to repair."""
        if self.strategy_id is None:
            return ''
        return (f'<REPAIR_STRATEGY id="{self.strategy_id}" version="{self.version}">\n'
                + self.instruction + '\n</REPAIR_STRATEGY>\n')

    def snapshot(self):
        return dict(strategy_id=self.strategy_id, reason=self.reason,
                    category=self.category, stage=self.stage, version=self.version,
                    pack_sha256=self.pack_sha256, instruction=self.instruction,
                    instruction_sha256=self.instruction_sha256)


@dataclass(frozen=True)
class StrategyPack:
    version: str
    templates: tuple[tuple[str, str], ...]

    def snapshot(self):
        return dict(version=self.version, templates=dict(self.templates))

    @property
    def sha256(self):
        return json_digest(self.snapshot())

    def select(self, diagnostic: Diagnostic):
        strategy = None
        reason = 'category_not_supported'
        if not diagnostic.repairable:
            reason = 'diagnostic_not_repairable'
        elif diagnostic.category in ROUTES:
            strategy = ROUTES[diagnostic.category]
            reason = 'category_route'
            if strategy == 'insufficient_evidence':
                reason = 'ambiguous_failure_category'
            elif _limited_feedback(diagnostic.feedback, diagnostic.category):
                strategy = 'insufficient_evidence'
                reason = 'feedback_missing_or_category_only'
        templates = dict(self.templates)
        instruction = (templates['common'] + '\n\n' + templates[strategy]) if strategy else ''
        return StrategySelection(strategy, reason, diagnostic.category, diagnostic.stage,
                                 self.version, self.sha256, instruction)


def _limited_feedback(feedback, category):
    """Recognize current validator fallbacks; not a semantic quality score."""
    text = feedback.strip()
    if not text:
        return True
    category_text = re.escape(category)
    return bool(re.fullmatch(
        rf'(?:(?:csim|synthesis):\s*)?{category_text}\.?'
        r'(?:\s+(?:Detailed diagnostics are not released by this task\.|'
        r'Structured evidence (?:unavailable|omitted by context budget)\.))?', text))


def load_pack(directory=None):
    """Read every template once. Fail explicitly for missing/empty templates."""
    root = Path(directory) if directory is not None else TEMPLATE_ROOT
    templates = []
    for name in TEMPLATE_NAMES:
        text = (root / (name + '.txt')).read_text(encoding='utf-8').strip()
        if not text:
            raise ValueError('Empty repair strategy template: ' + name)
        templates.append((name, text))
    return StrategyPack(VERSION, tuple(templates))
