"""Versioned, conservative diagnostic retrieval; no model or hidden inputs."""
import re
import json

from rag.annotation import diagnostic_annotation


STRATEGY = 'diagnostic_v1'
STOP = set('the and for from with this that error warning note fatal hls vitis csim '
           'compile compilation synthesis failed failure function functions type '
           'no not has have member named use used using cannot could does declared '
           'candidate cpp source file line column diagnostic released task'.split())


def _signature(problem, feedback, cleaned):
    """Classify only diagnostic/source signatures; never infer a repair."""
    text = '\n'.join(value for value in (feedback, cleaned, problem) if value)
    lower = text.lower()
    if re.search(r'\bunexpected interface offset\b', lower) and re.search(
            r'\b(?:slave|direct|off)\b', lower):
        return dict(family='interface_offset', exact_terms=['offset', 'slave', 'direct', 'off'],
                    source_constructs=['m_axi', 'offset'])
    if re.search(r'\bno matching function\b', lower) and re.search(r"\bap_(?:u)?int\s*<\s*\d+\s*>", lower):
        single_arg = bool(re.search(r'\b[A-Za-z_]\w*\s*\(\s*(?:\d+|[A-Za-z_]\w*)\s*\)', text))
        range_arg = bool(re.search(r'\(\s*(?:\d+|[A-Za-z_]\w*)\s*,\s*(?:\d+|[A-Za-z_]\w*)\s*\)', text))
        if single_arg or range_arg:
            return dict(family='ap_int_bit_selection', exact_terms=['ap_uint', 'ap_int'],
                        source_constructs=['single_arg_call' if single_arg else 'range_call'],
                        required_markers=['bit selection', 'operator []', 'range selection',
                                          'operator ()'])
        return dict(family='ap_int_no_matching_function', exact_terms=['ap_uint', 'ap_int'],
                    source_constructs=[], required_markers=[])
    if re.search(r'\buse of undeclared identifier\b', lower):
        return dict(family='undeclared_identifier', exact_terms=[], source_constructs=[],
                    hard_abstain=True)
    if re.search(r'\bexpected\s+[\'"`]?;[\'"`]?', lower) or \
            re.search(r'\bexpected\s+[\'"`]?\}[\'"`]?', lower):
        return dict(family='generic_cpp_syntax', exact_terms=[], source_constructs=[],
                    hard_abstain=True)
    missing_header = re.search(r"['\"<]([^'\">]+\.(?:h|hpp))['\">]\s+file not found", lower)
    if missing_header:
        return dict(family='missing_header', exact_terms=[], source_constructs=[],
                    required_markers=['#include', '.h', '.hpp'],
                    missing_header=missing_header.group(1))
    if re.search(r'\binvalid digit\b|\binvalid suffix\b|\bredefinition of\b|\bexcess elements in array initializer\b', lower):
        return dict(family='generic_cpp_syntax', exact_terms=[], source_constructs=[],
                    hard_abstain=True)
    return dict(family='generic', exact_terms=[], source_constructs=[])


def _signature_gate(record, plan):
    """Apply a conservative applicability gate after lexical retrieval."""
    signature = plan.get('signature', {})
    family = signature.get('family', 'generic')
    text = (record.get('title', '') + ' ' + record.get('text', '')).lower()
    if signature.get('hard_abstain'):
        return False, 'generic_cpp_diagnostic', dict(family=family, matched=False)
    if family == 'interface_offset':
        required = ('offset=slave', 'offset=direct', 'offset=off')
        matched = all(term in text for term in required)
        return matched, 'signature_match' if matched else 'missing_interface_offset_signature', \
            dict(family=family, matched=matched, required=list(required))
    if family == 'ap_int_bit_selection':
        markers = signature.get('required_markers', ())
        matched = any(marker in text for marker in markers) and \
            bool(re.search(r'ap[_ ]?(?:u)?int|ap_int', text))
        return matched, 'signature_match' if matched else 'missing_ap_int_bit_selection_signature', \
            dict(family=family, matched=matched, required=list(markers))
    if family == 'ap_int_no_matching_function':
        return False, 'ambiguous_ap_int_signature', dict(family=family, matched=False)
    if family == 'missing_header':
        markers = signature.get('required_markers', ())
        missing_header = signature.get('missing_header', '')
        corrective_headers = {'hls.h': ('hls_stream.h', 'ap_int.h', 'hls_math.h')}
        header_match = missing_header in text and any(header in text for header in corrective_headers.get(missing_header, ()))
        matched = header_match and any(marker in text for marker in markers)
        return matched, 'signature_match' if matched else 'missing_header_signature', \
            dict(family=family, matched=matched, required=list(markers), missing_header=missing_header)
    return True, 'no_signature_gate', dict(family=family, matched=None)


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
    plan['signature'] = _signature(problem, feedback, cleaned)
    plan['annotation'] = diagnostic_annotation(feedback, plan, category, feedback_policy)
    return query, plan


def assess(record, plan):
    """Lexical evidence gate, not a calibrated semantic confidence score."""
    text = record['title'] + ' ' + record['text'] + ' ' + ' '.join(record.get('aliases', []))
    tokens = set(re.findall(r'[a-z_][a-z0-9_]*', text.lower()))
    overlap = sorted(tokens.intersection(plan['diagnostic_terms']))
    missing = sorted(set(plan['required_terms']) - tokens)
    lexical = not missing and len(overlap) >= 2
    signature_ok, signature_reason, gate = _signature_gate(record, plan)
    accepted = not missing and signature_ok and (lexical or signature_reason == 'signature_match')
    from rag.annotation import candidate_annotation
    decision = dict(id=record['id'], accepted=accepted, overlap=overlap,
                    missing_required=missing,
                    reason=('diagnostic_term_match' if accepted and signature_reason == 'no_signature_gate'
                            else signature_reason if not accepted else 'signature_match'),
                    lexical_match=lexical, signature_gate=gate)
    decision['annotation'] = candidate_annotation(record, plan, overlap, missing, gate)
    return accepted, decision
