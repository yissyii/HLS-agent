"""Source-aware prompts with an explicitly conservative fallback budget."""
from agent.core.contracts import PromptBundle, digest, json_digest
from agent.context.prompts import load_prompts
from serve.inference import Failure


def build(task, runtime, policy, candidate=None, diagnostic=None, skills=(), raw_initial=False, *, templates=None):
    templates = templates if templates is not None else load_prompts()
    problem = task.problem.decode('utf-8')
    provenance = [{'kind': 'problem', 'sha256': digest(task.problem)}]
    raw = raw_initial and candidate is None
    stage = 'raw_initial' if raw else 'repair' if candidate is not None else 'initial'
    system = '' if raw else templates.system
    if raw:
        mandatory = problem
    else:
        instruction = templates.repair if candidate is not None else templates.initial
        provenance.extend([
            {'kind': 'system_prompt', 'version': templates.version, 'sha256': digest(system.encode('utf-8'))},
            {'kind': 'stage_prompt', 'stage': stage, 'sha256': digest(instruction.encode('utf-8'))},
        ])
        mandatory = (f'<STAGE>{stage}</STAGE>\n' + instruction + '\n'
                     f'Target: {runtime["hls"]["part"]}; clock: {runtime["hls"]["clock_ns"]} ns.\n'
                     '<PROBLEM>\n' + problem + '\n</PROBLEM>\n')
        if task.manifest:
            mandatory += '<TOP_FUNCTION>' + task.manifest['top_function'] + '</TOP_FUNCTION>\n'
        for material in task.materials:
            if material.model_visible:
                mandatory += f'<PUBLIC_FILE name="{material.name}">\n{material.content.decode("utf-8")}\n</PUBLIC_FILE>\n'
                provenance.append({'kind': 'public_file', 'name': material.name, 'sha256': digest(material.content)})
    if candidate is not None:
        mandatory += '<CURRENT_SOURCE>\n' + candidate.source + '\n</CURRENT_SOURCE>\n'
        provenance.append({'kind': 'candidate', 'sha256': candidate.sha256})
    feedback = diagnostic.feedback[:policy['diagnostic_max_chars']] if diagnostic else ''
    selected = list(skills)
    available = runtime['model']['context_tokens'] - runtime['model']['max_tokens'] - policy['context_safety_tokens']
    system_bytes = len(system.encode('utf-8'))
    overhead = 64 * (2 if system else 1)  # Heuristic role/template allowance, not exact tokenization.

    def budgeted_bytes(text):
        return system_bytes + len(text.encode('utf-8')) + overhead

    def assemble():
        text = mandatory
        if diagnostic:
            text += f'\n<DIAGNOSTIC category="{diagnostic.category}">\n{feedback}\n</DIAGNOSTIC>\n'
        for rule in selected:
            text += '\n<REPAIR_RULE id="' + rule['id'] + '">\n' + rule['guidance'] + '\n</REPAIR_RULE>\n'
        return text

    text = assemble()
    # Byte length is deliberately conservative for typical byte tokenizers, but
    # is not claimed to be an exact count or a guarantee for arbitrary templates.
    while budgeted_bytes(text) > available and selected:
        selected.pop()
        text = assemble()
    while budgeted_bytes(text) > available and len(feedback) > 256:
        feedback = feedback[:max(256, len(feedback) // 2)]
        text = assemble()
    if budgeted_bytes(text) > available:
        raise Failure('context_budget_exceeded', 'Required context exceeds conservative budget; no silent source truncation')
    if diagnostic:
        provenance.append({'kind': 'diagnostic', 'sha256': digest(feedback.encode()),
                           'fingerprint': diagnostic.fingerprint, 'trimmed': feedback != diagnostic.feedback})
    provenance += [{'kind': 'skill', 'id': rule['id'], 'sha256': rule['sha256']} for rule in selected]
    messages = ([{'role': 'system', 'content': system}] if system else []) + [{'role': 'user', 'content': text}]
    return PromptBundle(text, provenance, {
        'method': 'conservative_utf8_bytes', 'token_count_verified': False,
        'input_bytes': system_bytes + len(text.encode('utf-8')), 'input_budget': available,
        'system_bytes': system_bytes, 'user_bytes': len(text.encode('utf-8')),
        'message_overhead_bytes': overhead, 'budgeted_input_bytes': budgeted_bytes(text),
        'stage': stage, 'prompt_templates_version': templates.version,
        'prompt_templates_sha256': templates.sha256, 'system_prompt_used': bool(system),
        'messages_sha256': json_digest(messages),
        'output_tokens': runtime['model']['max_tokens'], 'safety_tokens': policy['context_safety_tokens'],
        'note': 'Local tokenizer/chat-template verification is not installed; service overflow remains a failure.',
    }, [r['id'] for r in selected], system=system)
