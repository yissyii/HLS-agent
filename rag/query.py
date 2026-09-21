"""Versioned, conservative diagnostic retrieval; no model or hidden inputs."""
import re
import json

from rag.annotation import diagnostic_annotation


STRATEGY = 'diagnostic_v1'
STOP = set('the and for from with this that error warning note fatal hls vitis csim '
           'compile compilation synthesis failed failure function functions type '
           'no not has have member named use used using cannot could does declared '
           'candidate cpp source file line column diagnostic released task'.split())


def clean_feedback(feedback):
    lines = []
    for line in feedback.splitlines():
        # Strip a compiler location, preserving its message and quoted API names.
        line = re.sub(r'^.*?\.(?:cpp|cc|c|hpp|h):\d+(?::\d+)?:\s*', '', line.strip())
        line = re.sub(r'(?:[A-Za-z]:)?[/\\](?:[^\s:]+[/\\])+[^\s:]+', '<path>', line)
        line = re.sub(r'\b\d{4}-\d{2}-\d{2}[T ][\d:.+Z-]+', '<time>', line)
        line = ' '.join(line.split())
        if line and line not in lines and not re.fullmatch(r'[\^~|\d ]+', line):
            lines.append(line)
    # Keep actual errors before notes/source snippets when the query is bounded.
    lines.sort(key=lambda line: 0 if re.search(r'\berror\b', line, re.I) else 1)
    return '\n'.join(lines)


def terms(text):
    return {t for t in re.findall(r'[a-z_][a-z0-9_]*', text.lower())
            if len(t) >= 3 and t not in STOP}


def query_plan(problem, feedback, category, feedback_policy, max_chars):
    original_feedback = feedback
    functional_skip = False
    if feedback_policy == 'functional_diagnostics' and category == 'functional_or_runtime_error':
        # Counterexample values help repair, but are not a useful manual query.
        # Only explicitly observed runtime messages provide a manual-search signal.
        try:
            report = json.loads(feedback.split('\n', 1)[1])
            messages = [e['observed']['message'] for e in report['events']
                        if e['failure_kind'] in ('runtime_exception', 'deadlock') and
                        isinstance(e.get('observed', {}).get('message'), str)]
        except (ValueError, IndexError, KeyError, TypeError):
            messages = []
        feedback = '\n'.join(messages)
        functional_skip = not messages
    cleaned = clean_feedback(feedback)
    stripped = re.sub(r'\b(?:error|csim|synthesis)\s*:', '', cleaned).strip(' .')
    reason = None
    if feedback_policy == 'category_only' or stripped == category or not cleaned:
        reason = 'insufficient_diagnostic'
    if functional_skip:
        reason = 'functional_feedback_without_manual_signal'
    prefix, middle = 'Vitis HLS diagnostic:\n', '\nTask context:\n'
    room = max_chars - len(prefix) - len(middle)
    problem_size = min(len(problem), 256, room // 4)
    diagnostic_size = min(len(cleaned), room - problem_size)
    query = prefix + cleaned[:diagnostic_size] + middle + problem[:problem_size]
    diagnostic_terms = sorted(terms(cleaned[:diagnostic_size]))
    # An explicit missing member must occur in a reference. Merely matching its
    # broad type (e.g. ap_uint) is insufficient evidence about that member.
    member = re.search(r"\bno member(?: named)?\s+['\"‘’]([A-Za-z_]\w*)['\"‘’]", cleaned, re.I)
    required = [member.group(1).lower()] if member else []
    if not diagnostic_terms:
        reason = reason or 'no_diagnostic_terms'
    plan = dict(strategy=STRATEGY, skip_reason=reason,
                diagnostic_terms=diagnostic_terms, required_terms=required,
                problem_trimmed=problem_size < len(problem),
                feedback_trimmed=diagnostic_size < len(cleaned),
                feedback_cleaned=cleaned != original_feedback,
                cleaned_feedback=cleaned)
    plan['annotation'] = diagnostic_annotation(feedback, plan, category, feedback_policy)
    return query, plan


def assess(record, plan):
    """Lexical evidence gate, not a calibrated semantic confidence score."""
    text = record['title'] + ' ' + record['text'] + ' ' + ' '.join(record.get('aliases', []))
    tokens = set(re.findall(r'[a-z_][a-z0-9_]*', text.lower()))
    overlap = sorted(tokens.intersection(plan['diagnostic_terms']))
    missing = sorted(set(plan['required_terms']) - tokens)
    accepted = not missing and len(overlap) >= 2
    from rag.annotation import candidate_annotation
    decision = dict(id=record['id'], accepted=accepted, overlap=overlap,
                    missing_required=missing,
                    reason='diagnostic_term_match' if accepted else 'insufficient_diagnostic_match')
    decision['annotation'] = candidate_annotation(record, plan, overlap, missing)
    return accepted, decision
