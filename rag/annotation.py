"""Provisional diagnostic and retrieval annotations for human review.

These scores describe observable evidence quality and lexical support.  They are
not relevance probabilities and must not silently change retrieval behavior.
Human labels belong in ``rag/annotations`` or ``rag/staging`` after reviewing
the emitted evidence files.
"""
import re


GENERIC_TERMS = frozenset({
    'array', 'candidate', 'compile', 'compilation', 'cpp', 'declared', 'error',
    'failed', 'failure', 'file', 'function', 'functions', 'hls', 'identifier', 'named',
    'no', 'not', 'source', 'static', 'synthesis', 'type', 'used', 'using',
    'undeclared', 'vitis', 'warning',
})


def _has_location(text):
    return bool(re.search(r'(?:^|\s)[^\s:]+\.(?:cpp|cc|c|hpp|h):\d+(?::\d+)?', text, re.M))


def _has_code(text):
    return bool(re.search(r'\b(?:HLS|SYNCHK|SIM|CSIM)\s*\d+(?:-\d+)+\b', text, re.I))


def _has_source_line(text):
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) < 2:
        return False
    return any((';' in line or '#' in line or '(' in line or ')' in line or
                '=' in line or '::' in line) for line in lines[1:])


def diagnostic_annotation(feedback, plan, category, feedback_policy):
    """Return a reviewable, provisional quality annotation for one query.

    The score is deliberately transparent: one point each for a released
    message, compiler code, source location, source line, and caret/note.  It
    measures diagnostic completeness, not whether a manual passage is correct.
    """
    feedback = feedback or ''
    cleaned = plan.get('cleaned_feedback', feedback)
    terms = set(plan.get('diagnostic_terms', ()))
    specific = sorted(terms - GENERIC_TERMS)
    features = {
        'has_message': bool(cleaned.strip()),
        'has_error_code': _has_code(feedback),
        'has_location': _has_location(feedback),
        'has_source_line': _has_source_line(feedback),
        'has_caret_or_note': bool(re.search(r'\^|~|\bnote:', feedback, re.I)),
    }
    score = sum(bool(value) for value in features.values())
    if plan.get('skip_reason'):
        signal = 'insufficient'
        action = 'abstain'
    elif _has_code(feedback) or len(specific) >= 2:
        signal = 'specific'
        action = 'search'
    elif specific:
        signal = 'ambiguous'
        action = 'judge'
    else:
        signal = 'generic'
        action = 'judge'
    return {
        'schema_version': 1,
        'status': 'provisional',
        'diagnostic_quality_score': score,
        'diagnostic_quality_max': 5,
        'diagnostic_quality_label': ('high' if score >= 4 else 'medium' if score >= 2 else 'low'),
        'signal_class': signal,
        'recommended_action': action,
        'specific_terms': specific,
        'generic_terms': sorted(terms.intersection(GENERIC_TERMS)),
        'features': features,
        'category': category,
        'feedback_policy': feedback_policy,
        'skip_reason': plan.get('skip_reason'),
        'human_label': None,
        'human_should_inject': None,
    }


def candidate_annotation(record, plan, overlap, missing):
    """Return lexical-support metadata without claiming semantic correctness."""
    required = set(plan.get('required_terms', ()))
    score = min(2, len(overlap))
    if required and not missing:
        score += 2
    return {
        'schema_version': 1,
        'status': 'provisional',
        'lexical_evidence_score': min(4, score),
        'lexical_evidence_max': 4,
        'label': 'strong_lexical_support' if score >= 3 else 'weak_or_generic_overlap',
        'record_id': record.get('id'),
        'human_label': None,
        'human_should_inject': None,
    }
