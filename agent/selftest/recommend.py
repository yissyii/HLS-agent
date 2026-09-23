"""Public-input-only rule recommendation. Never approves or binds a suite."""
import argparse
import json
from pathlib import Path
import time

from agent.artifacts.writer import Artifacts
from agent.core.contracts import PromptBundle, digest, json_digest
from serve.inference import Failure, load_config
from .rules import RULES, RULE_VERSION, check_applicability
from .spec import PublicTask, SelftestError, evidence, integer, keys, parse_json, require, text, type_info

PROMPT = Path(__file__).parent/'prompts/recommend.md'
SELECTOR_VERSION = '0.1.0'


def make_prompt(task):
    task.validate()
    payload = dict(public_problem=task.problem, public_interface=task.interface,
                   registry=RULES, rule_version=RULE_VERSION,
                   applicability='One unsigned non-bool scalar, full width-bit input domain, width 1..16, unsigned output with sufficient range. Stateless only.')
    return PromptBundle(json.dumps(payload, ensure_ascii=False, sort_keys=True),
                        [{'kind':'public_inputs', **task.snapshot()}],
                        dict(selector_version=SELECTOR_VERSION, candidate_visible=False, oracle_visible=False),
                        system=PROMPT.read_text(encoding='utf-8'))


def validate_proposal(value, task):
    signature = task.validate()
    keys(value, {'schema_version','decision','rule_id','width','input_domain','evidence','rationale','uncertainties'}, 'rule proposal')
    require(type(value['schema_version']) is int and value['schema_version']==1, 'Unsupported proposal schema')
    require(isinstance(value['decision'], str) and value['decision'] in {'propose','unsupported','uncertain'}, 'Invalid decision')
    evidence(value['evidence'], task)
    require(any(e['source']=='problem' for e in value['evidence']), 'Rule semantics need problem evidence, not just interface')
    text(value['rationale'], 'rationale')
    require(isinstance(value['uncertainties'], list) and len(value['uncertainties'])<=16, 'Invalid uncertainties')
    for item in value['uncertainties']:
        text(item, 'uncertainty')
    if value['decision']=='propose':
        require(not value['uncertainties'], 'Uncertain proposal must abstain')
        require(isinstance(value['rule_id'], str) and value['rule_id'] in RULES, 'Unknown rule')
        integer(value['width'],1,16,'rule width')
        require(len(signature['inputs'])==1, 'Rules require one input')
        domain = value['input_domain']
        require(isinstance(domain,list) and len(domain)==2, 'Domain must be [low,high]')
        low,high = type_info(signature['inputs'][0]['type'])
        integer(domain[0],low,high,'domain low')
        integer(domain[1],domain[0],high,'domain high')
        contract = dict(inputs=[dict(signature['inputs'][0],domain=domain)], output_type=signature['output_type'])
        check_applicability(value['rule_id'],value['width'],contract)
    else:
        require(all(value[k] is None for k in ('rule_id','width','input_domain')), 'Abstention must not bind a rule')
        if value['decision']=='uncertain':
            require(bool(value['uncertainties']), 'Uncertain decision must list missing facts')
    return value


def recommend_rule(task, output, *, model=None, runtime=None, response=None, timeout=180):
    artifacts = Artifacts(output)
    started = time.monotonic()
    result = dict(schema_version=1, selector_version=SELECTOR_VERSION, status='failed',
                  model_requests=0, candidate_visible=False, oracle_visible=False,
                  semantic_match='unverified', manual_review_required=True, automatic_binding_allowed=False,
                  origin='recorded_response' if response is not None else 'model')
    try:
        require(type(timeout) in (int,float) and 0<timeout<=3600, 'Invalid recommendation timeout')
        require((model is not None)!=(response is not None), 'Choose one live model or recorded response')
        prompt = make_prompt(task)
        result.update(public_inputs=task.snapshot(), prompt_sha256=digest(prompt.system.encode()))
        artifacts.bytes('problem.txt',task.problem.encode())
        artifacts.bytes('interface.h',task.interface.encode())
        artifacts.bytes('prompt.txt',prompt.text.encode())
        artifacts.bytes('system_prompt.txt',prompt.system.encode())
        if model is not None:
            require(runtime is not None, 'Runtime required')
            require(len((prompt.text+prompt.system).encode())+512 <= runtime['model']['context_tokens']-runtime['model']['max_tokens'], 'Conservative context budget exceeded')
            directory=artifacts.root/'generation'
            directory.mkdir()
            result['model_requests']=1
            metadata=model.generate(prompt,runtime,directory,started+timeout)
            artifacts.json('metadata.json',metadata)
            if metadata.get('status')!='passed':
                raise Failure(metadata.get('category','generation_error'),'Rule recommendation request failed')
            value=parse_json((directory/'response.txt').read_text(encoding='utf-8'))
        else:
            value=response
        artifacts.json('proposal.json',value)
        result['proposal']=validate_proposal(value,task)
        result['status']='recommendation_unreviewed'
    except (SelftestError,Failure,OSError,ValueError,TypeError,KeyError) as error:
        result.update(category=getattr(error,'category','invalid_recommendation'),message=str(error))
    result['elapsed_seconds']=round(time.monotonic()-started,3)
    result['recommendation_id']=json_digest(result)
    artifacts.json('recommendation.json',result)
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('problem'); parser.add_argument('interface'); parser.add_argument('output')
    mode=parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--config'); mode.add_argument('--response-file')
    args=parser.parse_args()
    task=PublicTask.load(args.problem,args.interface)
    if args.config:
        from serve.agent_model import ModelClient
        result=recommend_rule(task,args.output,model=ModelClient(),runtime=load_config(args.config,allow_external=True))
    else:
        result=recommend_rule(task,args.output,response=parse_json(Path(args.response_file).read_text(encoding='utf-8')))
    print(json.dumps(result,ensure_ascii=False,indent=2))
    raise SystemExit(0 if result['status']=='recommendation_unreviewed' else 2)
