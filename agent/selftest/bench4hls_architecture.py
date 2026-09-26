"""Typed scalar/state specification -> Python oracle -> deterministic C++ vectors.

The model cannot supply C++ or expected-value tables. No eval or external inputs.
Public semantics remain unverified; this experimental compiler does not prove them.
"""
import ast
import itertools
import json
import math
import operator
from pathlib import Path
import random
import re

from agent.core.contracts import PromptBundle,digest,json_digest
from serve.inference import Failure
from .spec import keys,parse_json,require

BIN={ast.Add:operator.add,ast.Sub:operator.sub,ast.Mult:operator.mul,ast.Mod:operator.mod,
     ast.BitAnd:operator.and_,ast.BitOr:operator.or_,ast.BitXor:operator.xor,
     ast.LShift:operator.lshift,ast.RShift:operator.rshift}
CMP={ast.Eq:operator.eq,ast.NotEq:operator.ne,ast.Lt:operator.lt,ast.LtE:operator.le,
     ast.Gt:operator.gt,ast.GtE:operator.ge}
UNA={ast.Invert:operator.invert,ast.Not:operator.not_,ast.USub:operator.neg,ast.UAdd:operator.pos}


def interface(problem,top):
    matches=re.findall(r'\bvoid\s+'+re.escape(top)+r'\s*\(([^()]*)\)',problem,re.S)
    require(len(matches)==1,'Exactly one public void prototype required')
    params=[]
    for raw in matches[0].split(','):
        m=re.fullmatch(r'\s*(bool|ap_(?:u?int)\s*<\s*\d+\s*>)\s*(&?)\s*([A-Za-z_]\w*)\s*',raw)
        require(m is not None,'Unsupported non-scalar interface')
        ctype,ref,name=m.groups(); ctype=re.sub(r'\s+','',ctype)
        width=1 if ctype=='bool' else int(re.search(r'\d+',ctype).group())
        require(1<=width<=1024,'Width outside supported range')
        signed=ctype.startswith('ap_int')
        params.append(dict(name=name,ctype=ctype,width=width,signed=signed,output=bool(ref)))
    require(len({p['name'] for p in params})==len(params),'Duplicate parameter names')
    inputs=[p for p in params if not p['output']]; outputs=[p for p in params if p['output']]
    require(1<=len(inputs)<=8 and 1<=len(outputs)<=8,'Unsupported input/output count')
    for index,p in enumerate(inputs): p['alias']='i'+str(index)
    return dict(top=top,params=params,inputs=inputs,outputs=outputs)


def supported_interface(problem,top):
    try: interface(problem,top); return True
    except ValueError: return False


class Expr:
    def __init__(self,source,names):
        require(isinstance(source,str) and 0<len(source)<=2000,'Invalid expression text')
        self.tree=ast.parse(source,mode='eval').body
        nodes=list(ast.walk(self.tree)); require(len(nodes)<=128,'Expression too complex')
        allowed=(ast.Constant,ast.Name,ast.Load,ast.BinOp,ast.UnaryOp,ast.Compare,ast.IfExp,
                 ast.BoolOp,ast.And,ast.Or,*BIN,*CMP,*UNA)
        for node in nodes:
            require(isinstance(node,allowed),'Unsupported expression syntax: '+type(node).__name__)
            if isinstance(node,ast.Name): require(node.id in names,'Unknown expression variable: '+node.id)
            if isinstance(node,ast.Constant):
                require(type(node.value) in (int,bool) and abs(node.value).bit_length()<=2048,'Invalid constant')

    def __call__(self,values):
        def visit(n):
            if isinstance(n,ast.Constant): result=n.value
            elif isinstance(n,ast.Name): result=values[n.id]
            elif isinstance(n,ast.IfExp):
                test=visit(n.test); result=None if test is None else visit(n.body if test else n.orelse)
            elif isinstance(n,ast.BoolOp):
                unknown=False
                for node in n.values:
                    v=visit(node)
                    if v is None: unknown=True; continue
                    if isinstance(n.op,ast.And) and not v: return 0
                    if isinstance(n.op,ast.Or) and v: return 1
                result=None if unknown else int(isinstance(n.op,ast.And))
            elif isinstance(n,ast.UnaryOp):
                v=visit(n.operand); result=None if v is None else UNA[type(n.op)](v)
            elif isinstance(n,ast.BinOp):
                a,b=visit(n.left),visit(n.right)
                if a is None or b is None: return None
                if isinstance(n.op,(ast.LShift,ast.RShift)): require(0<=b<=1024,'Invalid shift')
                if isinstance(n.op,ast.Mod): require(b>0,'Modulo requires positive divisor')
                result=BIN[type(n.op)](a,b)
            elif isinstance(n,ast.Compare):
                a=visit(n.left); result=True
                for op,node in zip(n.ops,n.comparators):
                    b=visit(node)
                    if a is None or b is None: return None
                    if not CMP[type(op)](a,b): result=False; break
                    a=b
            else: raise ValueError('Unsupported expression')
            require(result is None or type(result) in (int,bool) and abs(result).bit_length()<=2048,'Expression magnitude overflow')
            return None if result is None else int(result)
        return visit(self.tree)


def bounds(p):
    return (-(1<<(p['width']-1)),(1<<(p['width']-1))-1) if p['signed'] else (0,(1<<p['width'])-1)


def typed(value,p):
    if value is None: return None
    if p.get('ctype')=='bool': return int(bool(value))
    value &= (1<<p['width'])-1
    if p.get('signed') and value>=(1<<(p['width']-1)): value-=1<<p['width']
    return value


def compile_spec(raw,problem,signature):
    keys(raw,{'schema_version','decision','state','outputs','sequences','excluded','reasons'},'typed specification')
    require(type(raw['schema_version']) is int and raw['schema_version']==1,'Unsupported version')
    for name in ('state','outputs','sequences','excluded','reasons'):
        require(isinstance(raw[name],list),'Expected list: '+name)
    require(len(raw['excluded'])<=12 and len(raw['reasons'])<=12,'Too many exclusions/reasons')
    require(all(isinstance(s,str) and 0<len(s)<=2000 for s in raw['excluded']+raw['reasons']),'Invalid reason')
    if raw['decision']=='abstain':
        require(raw['reasons'] and not raw['state'] and not raw['outputs'] and not raw['sequences'],'Invalid abstention')
        return None,None
    require(raw['decision'] in ('ready','partial') and not raw['reasons'],'Invalid decision')
    require(bool(raw['excluded'])==(raw['decision']=='partial'),'Partial exclusions must be explicit')
    require(len(raw['state'])<=8 and len(raw['outputs'])==len(signature['outputs']),'Invalid state/output count')
    def evidence(item):
        quotes=item['evidence']
        require(isinstance(quotes,list) and 1<=len(quotes)<=4,'Missing evidence')
        require(all(isinstance(q,str) and q.strip() and q in problem for q in quotes),'Evidence must quote public problem')
    names={p['alias'] for p in signature['inputs']}
    for index,item in enumerate(raw['state']):
        keys(item,{'name','width','next','evidence'},'state variable'); evidence(item)
        require(item['name']=='s'+str(index),'State variables must be s0,s1,...')
        require(type(item['width']) is int and 1<=item['width']<=1024,'Invalid state width')
        names.add(item['name'])
    transitions=[Expr(s['next'],names) for s in raw['state']]
    output_names=names|{'n'+str(i) for i in range(len(transitions))}
    output_expr={}
    for item in raw['outputs']:
        keys(item,{'name','expr','when','evidence'},'output expression'); evidence(item)
        require(item['name'] not in output_expr,'Duplicate output')
        output_expr[item['name']]=(Expr(item['expr'],output_names),Expr(item['when'],output_names))
    require(set(output_expr)=={p['name'] for p in signature['outputs']},'Output names differ from public interface')
    require(len(raw['sequences'])<=16,'Too many sequences')
    stimuli=[]
    for sequence in raw['sequences']:
        keys(sequence,{'inputs','repeat'},'sequence')
        require(type(sequence['repeat']) is int and 1<=sequence['repeat']<=128,'Invalid repetition')
        values=sequence['inputs']; require(isinstance(values,list) and len(values)==len(signature['inputs']),'Input arity mismatch')
        for v,p in zip(values,signature['inputs']):
            lo,hi=bounds(p); require(type(v) is int and lo<=v<=hi,'Input outside declared type range')
        stimuli.extend([values]*sequence['repeat'])
    require(len(stimuli)<=256,'Too many directed calls')
    domains=[bounds(p) for p in signature['inputs']]
    if math.prod(hi-lo+1 for lo,hi in domains)<=256:
        stimuli.extend([list(v) for v in itertools.product(*(range(lo,hi+1) for lo,hi in domains))])
    else:
        for selector in (0,1,2,3):
            stimuli.append([(lo,hi,0,min(1,hi))[selector] for lo,hi in domains])
        rng=random.Random(20260925)
        stimuli.extend([[rng.randint(lo,hi) for lo,hi in domains] for _ in range(64)])
    state={s['name']:None for s in raw['state']}; vectors=[]; counts=CounterLike(signature)
    for inputs in stimuli:
        env={p['alias']:v for p,v in zip(signature['inputs'],inputs)}; env.update(state)
        next_state={s['name']:typed(expr(env),s) for s,expr in zip(raw['state'],transitions)}
        env.update({'n'+str(i):next_state[s['name']] for i,s in enumerate(raw['state'])})
        expected={}
        for p in signature['outputs']:
            expr,when=output_expr[p['name']]
            if when(env):
                value=typed(expr(env),p)
                if value is not None: expected[p['name']]=value; counts[p['name']]+=1
        vectors.append(dict(inputs=inputs,expected=expected)); state=next_state
    require(all(counts.values()),'Every declared output needs at least one known check')
    return vectors,dict(calls=len(vectors),assertions=sum(counts.values()),per_output=counts,
        initial_state='unknown_until_synchronized',semantic_correctness='unverified')


def CounterLike(signature):
    return {p['name']:0 for p in signature['outputs']}


def literal(value,p):
    if p['ctype']=='bool': return 'true' if value else 'false'
    bits=format(value & ((1<<p['width'])-1),'b')
    return p['ctype']+'("'+bits+'", 2)'


def render(signature,vectors):
    lines=['#include <iostream>','#include <ap_int.h>',
        'void '+signature['top']+'('+', '.join(p['ctype']+(' &' if p['output'] else ' ')+p['name'] for p in signature['params'])+');',
        'int main(){ int failures=0;']
    for index,case in enumerate(vectors):
        lines.append('{')
        for p in signature['outputs']: lines.append(p['ctype']+' actual_'+p['name']+'=0;')
        values={p['name']:literal(v,p) for p,v in zip(signature['inputs'],case['inputs'])}
        args=[('actual_'+p['name']) if p['output'] else values[p['name']] for p in signature['params']]
        lines.append(signature['top']+'('+', '.join(args)+');')
        for p in signature['outputs']:
            name=p['name']
            if name not in case['expected']: continue
            value=case['expected'][name]
            lines.append('if(actual_'+name+' != '+literal(value,p)+'){ ++failures; if(failures<=20)')
            prefix=json.dumps(dict(kind='output_mismatch',index=index,signal=name,inputs=[str(v) for v in case['inputs']],expected=str(value)),separators=(',',':'))[:-1]+',"actual":"'
            lines.append('std::cout << '+json.dumps('ZCOMP_FUNCTIONAL '+prefix)+' << actual_'+name+' << "\\\"}" << std::endl; }')
        lines.append('}')
    lines.append('return failures ? 1 : 0; }')
    return '\n'.join(lines)+'\n'


def generate_architecture(problem,top,output,*,model,runtime,deadline):
    directory=Path(output); directory.mkdir(parents=True,exist_ok=False)
    result=dict(status='failed',generation_mode='typed_architecture',model_requests=0,
                candidate_visible=False,official_testbench_visible=False,semantic_correctness='unverified',automatic_acceptance_allowed=False)
    try:
        signature=interface(problem.decode('utf-8'),top)
        system=(Path(__file__).parent/'prompts/bench4hls_architecture.md').read_text(encoding='utf-8')
        prompt=PromptBundle(json.dumps(dict(public_problem=problem.decode('utf-8'),interface=signature),ensure_ascii=False),
            [dict(kind='public_problem',sha256=digest(problem))],dict(phase='typed_specification',candidate_visible=False,official_testbench_visible=False),system=system)
        result['model_requests']=1; result['metadata']=model.generate(prompt,runtime,directory,deadline)
        if result['metadata'].get('status')!='passed':
            raise Failure(result['metadata'].get('category','generation_error'),'Specification generation failed')
        raw=parse_json((directory/'response.txt').read_text(encoding='utf-8'))
        vectors,coverage=compile_spec(raw,problem.decode('utf-8'),signature)
        (directory/'specification.json').write_text(json.dumps(raw,indent=2)+'\n',encoding='utf-8')
        if vectors is None: result.update(status='abstained',reasons=raw['reasons'])
        else:
            source=render(signature,vectors)
            (directory/'vectors.json').write_text(json.dumps(vectors,indent=2)+'\n',encoding='utf-8')
            (directory/'selftest.cpp').write_bytes(source.encode('utf-8'))
            result.update(status='generated_unreviewed',coverage_scope=raw['decision'],excluded_behaviors=raw['excluded'],coverage=coverage,source_sha256=digest(source.encode()),specification_sha256=json_digest(raw))
    except (Failure,ValueError,TypeError,KeyError,OSError,SyntaxError,RecursionError) as error:
        result.update(category=getattr(error,'category','typed_specification_invalid'),message=str(error))
    result['receipt_id']=json_digest(result)
    (directory/'result.json').write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
    return result
