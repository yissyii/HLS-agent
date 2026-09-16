"""Source-aware prompts with an explicitly conservative fallback budget."""
from agent.core.contracts import PromptBundle, digest
from serve.inference import Failure


def build(task, runtime, policy, candidate=None, diagnostic=None, skills=(), raw_initial=False):
    problem = task.problem.decode('utf-8')
    provenance = [{'kind': 'problem', 'sha256': digest(task.problem)}]
    if raw_initial and candidate is None:
        mandatory = problem
    else:
        mandatory = ('Write a complete Vitis HLS C++ source file. Return source only, optionally in one cpp fence. '
                     'Preserve the exact required interface and behavior. Do not output a testbench or scripts.\n'
                     f'Target: {runtime["hls"]["part"]}; clock: {runtime["hls"]["clock_ns"]} ns.\n'
                     '<PROBLEM>\n' + problem + '\n</PROBLEM>\n')
        if task.manifest:
            mandatory += '<TOP_FUNCTION>' + task.manifest['top_function'] + '</TOP_FUNCTION>\n'
        for material in task.materials:
            if material.model_visible:
                mandatory += f'<PUBLIC_FILE name="{material.name}">\n{material.content.decode("utf-8")}\n</PUBLIC_FILE>\n'
                provenance.append({'kind': 'public_file', 'name': material.name, 'sha256': digest(material.content)})
    if candidate is not None:
        mandatory += '\nRepair the following candidate using the permitted diagnostic. Return its complete replacement.\n'
        mandatory += '<CURRENT_SOURCE>\n' + candidate.source + '\n</CURRENT_SOURCE>\n'
        provenance.append({'kind': 'candidate', 'sha256': candidate.sha256})
    feedback = diagnostic.feedback[:policy['diagnostic_max_chars']] if diagnostic else ''
    selected = list(skills)
    available = runtime['model']['context_tokens'] - runtime['model']['max_tokens'] - policy['context_safety_tokens']

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
    while len(text.encode('utf-8')) > available and selected:
        selected.pop()
        text = assemble()
    while len(text.encode('utf-8')) > available and len(feedback) > 256:
        feedback = feedback[:max(256, len(feedback) // 2)]
        text = assemble()
    if len(text.encode('utf-8')) > available:
        raise Failure('context_budget_exceeded', 'Required context exceeds conservative budget; no silent source truncation')
    if diagnostic:
        provenance.append({'kind': 'diagnostic', 'sha256': digest(feedback.encode()),
                           'fingerprint': diagnostic.fingerprint, 'trimmed': feedback != diagnostic.feedback})
    provenance += [{'kind': 'skill', 'id': rule['id'], 'sha256': rule['sha256']} for rule in selected]
    return PromptBundle(text, provenance, {
        'method': 'conservative_utf8_bytes', 'token_count_verified': False,
        'input_bytes': len(text.encode('utf-8')), 'input_budget': available,
        'output_tokens': runtime['model']['max_tokens'], 'safety_tokens': policy['context_safety_tokens'],
        'note': 'Local tokenizer/chat-template verification is not installed; service overflow remains a failure.',
    }, [r['id'] for r in selected])
