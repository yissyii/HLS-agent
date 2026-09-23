"""Frozen, synthetic auditor stress study. No LLM, no edits to the system under test.

Independent C++ labeling exhausts the declared domain without importing rules.py.
This is not a third-party blind evaluation or an end-to-end model benchmark.
"""
import argparse
import ast
import copy
import itertools
import json
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent.artifacts.writer import Artifacts
from agent.core.contracts import digest, json_digest
from agent.selftest.audit import audit_suite
from agent.selftest.generate import generate_suite
from agent.selftest.spec import PublicTask


FAMILIES = ('gray_decode', 'reverse_bits', 'popcount')
WIDTHS = (1, 2, 4, 8, 12, 16)
PRIMARY_SEED = 20260923
EXTRA_SEEDS = (7, 101)


def expression(rule, width, alternate=False):
    if rule == 'gray_decode':
        if not alternate:
            return ' ^ '.join(f'(x >> {i})' for i in range(width))
        value = 'x'
        shift = 1
        while shift < width:
            value = f'(({value}) ^ (({value}) >> {shift}))'
            shift *= 2
        return value
    if rule == 'popcount':
        if not alternate:
            # Nibble lookup is bounded by a 64-bit constant; unlike a 16-term bit sum,
            # this representation fits the existing 128-node expression budget.
            return ' + '.join(f'((0x4332322132212110 >> (((x >> {i}) & 15) * 4)) & 15)'
                              for i in range(0, width, 4))
        v = '(x - ((x >> 1) & 21845))'
        v = f'((({v}) & 13107) + ((({v}) >> 2) & 13107))'
        v = f'((({v}) + (({v}) >> 4)) & 3855)'
        return f'((({v}) * 257) >> 8) & 31'
    def reverse_byte(v):
        return f'(((((({v}) * 2050) & 139536) | ((({v}) * 32800) & 558144)) * 65793) >> 16) & 255'
    if not alternate and width <= 8:
        return ' | '.join(f'(((x >> {i}) & 1) << {width-1-i})' for i in range(width))
    hi = f'(({reverse_byte("x & 255")}) << 8)'
    lo = f'({reverse_byte("x >> 8")})'
    core = f'(({hi}) | ({lo}))' if not alternate else f'(({lo}) | ({hi}))'
    return f'({core}) >> {16-width}'


def catalog():
    rows, duplicates = [], []
    for rule, width in itertools.product(FAMILIES, WIDTHS):
        core = expression(rule, width)
        mask = (1 << width)-1
        variants = [('correct_primary', core, 'correct'),
                    ('correct_alternate', expression(rule, width, True), 'correct'),
                    ('zero', '0', 'mutation'), ('identity', 'x', 'mutation'),
                    ('flip_low_bit', f'({core}) ^ 1', 'mutation'),
                    ('drop_top_output_bit', f'({core}) & {mask >> 1}', 'mutation')]
        for point in (3, 181, 0xBEEF):
            point &= mask
            variants.append((f'singleton_{point}', f'({core}) ^ (1 if x == {point} else 0)', 'mutation'))
        seen = set()
        for name, source, intended in variants:
            row = dict(id=f'{rule}_w{width}_{name}', rule=rule, width=width, expression=source,
                       intended=intended, singleton=name.startswith('singleton_'))
            if row['id'] in seen:
                duplicates.append(row['id'])
                continue
            seen.add(row['id'])
            rows.append(row)
    return rows, duplicates


def cpp_expression(source):
    """Translate only this script's trusted fixture grammar, not arbitrary user code."""
    binary = {ast.Add:'+', ast.Sub:'-', ast.Mult:'*', ast.BitXor:'^', ast.BitOr:'|', ast.BitAnd:'&', ast.LShift:'<<', ast.RShift:'>>'}
    def visit(n):
        if isinstance(n, ast.Constant) and type(n.value) is int and 0 <= n.value < 2**64:
            return str(n.value)+'ULL'
        if isinstance(n, ast.Name) and n.id == 'x': return 'x'
        if isinstance(n, ast.BinOp) and type(n.op) in binary:
            return f'({visit(n.left)} {binary[type(n.op)]} {visit(n.right)})'
        if isinstance(n, ast.Compare) and len(n.ops)==1 and isinstance(n.ops[0], ast.Eq):
            return f'({visit(n.left)} == {visit(n.comparators[0])})'
        if isinstance(n, ast.IfExp): return f'({visit(n.test)} ? {visit(n.body)} : {visit(n.orelse)})'
        raise ValueError('Unsupported trusted fixture grammar: '+type(n).__name__)
    return visit(ast.parse(source, mode='eval').body)


def truth_program(rows):
    declarations = '\n'.join(f'uint64_t f{i}(uint64_t x) {{return {cpp_expression(r["expression"])};}}' for i, r in enumerate(rows))
    entries = ',\n'.join(f'{{"{r["id"]}", {FAMILIES.index(r["rule"])}, {r["width"]}, f{i}}}' for i,r in enumerate(rows))
    return '''#include <cstdint>
#include <iostream>
#include <vector>
''' + declarations + '''
struct Case { const char* id; unsigned rule; unsigned width; uint64_t (*fn)(uint64_t); };
Case cases[] = {
''' + entries + '''
};
int main() {
  for (const auto &c: cases) {
    const unsigned limit = 1u << c.width;
    std::vector<uint64_t> gold(limit);
    if (c.rule == 0) {
      // Build inverse table by enumerating the ENCODER, not cumulative Gray decoding.
      for (unsigned n=0;n<limit;++n) gold[n ^ (n/2)] = n;
    } else if (c.rule == 1) {
      for (unsigned x=0;x<limit;++x) {
        unsigned n=x, y=0;
        for (unsigned bit=0;bit<c.width;++bit) {y=2*y+n%2; n/=2;}
        gold[x]=y;
      }
    } else {
      for (unsigned x=1;x<limit;++x) gold[x]=gold[x/2]+x%2;
    }
    unsigned count=0, first=0; uint64_t got=0, want=0;
    for (unsigned x=0;x<limit;++x) {
      uint64_t y=c.fn(x);
      if(y!=gold[x]) { if(count==0) {first=x;got=y;want=gold[x];} ++count; }
    }
    std::cout << "{\\"id\\":\\"" << c.id << "\\",\\"domain_size\\":" << limit
              << ",\\"mismatches\\":" << count << ",\\"first_input\\":" << first
              << ",\\"expected\\":" << want << ",\\"actual\\":" << got << "}\\n";
  }
}
'''


def snapshot():
    paths = list((ROOT/'agent/selftest').glob('*.py')) + list((ROOT/'agent/selftest/prompts').glob('*.md'))
    paths += [ROOT/'agent/artifacts/writer.py', ROOT/'agent/core/contracts.py', ROOT/'serve/inference.py', Path(__file__)]
    return {p.relative_to(ROOT).as_posix():digest(p.read_bytes()) for p in sorted(paths)}


def task_and_response(rule, width, source, *, input_type='uint16_t', domain=None, second_input=False):
    mask = (1 << width)-1
    domain = domain or [0, mask]
    definitions = {
        'gray_decode': 'Decode reflected Gray code into the unsigned binary value. The input is formed by encoding n as n XOR floor(n/2). Return that n.',
        'reverse_bits': f'Reverse exactly {width} bits: input bit i becomes output bit {width-1}-i, including leading zero positions.',
        'popcount': f'Return how many one bits occur in the {width}-bit representation of x.'}
    problem = f'Stateless function check_word. {definitions[rule]} Legal input x is in [{domain[0]},{domain[1]}].'
    if second_input: problem += ' Input dummy is in [0,1] and is ignored.'
    header = f'#include <stdint.h>\nuint16_t check_word({input_type} x' + (', uint16_t dummy' if second_input else '') + ');\n'
    evidence = [dict(source='problem', quote=problem)]
    inputs = [dict(name='x', type=input_type, domain=domain, evidence=evidence)]
    if second_input: inputs.append(dict(name='dummy', type='uint16_t', domain=[0,1], evidence=evidence))
    contract = dict(schema_version=1, top_function='check_word', state='stateless', inputs=inputs,
                    output_type='uint16_t', rules=[dict(id='R1', description=problem, evidence=evidence)], uncertainties=[])
    # One seed input makes a frozen executable artifact; the auditor independently
    # chooses its own test points. This is a constructed fixture, NOT generated-model quality.
    plan = dict(schema_version=2, oracle=dict(expression=source, rule_ids=['R1']),
                explicit_cases=[dict(inputs=[0,0] if second_input else [0], rule_ids=['R1'])],
                sampling=dict(mode='explicit', seed=PRIMARY_SEED, random_cases=0))
    return PublicTask(problem, header), dict(contract=contract, test_plan=plan)


def judge_counts(rows):
    out = {}
    for mode in ('default', 'exhaustive'):
        tested = [r for r in rows if r.get('label') in ('correct', 'incorrect')]
        correct = [r for r in tested if r['label']=='correct']
        wrong = [r for r in tested if r['label']=='incorrect']
        def status(r): return r.get('audits', {}).get(mode, {}).get('status', 'not_materialized')
        def count(group, s): return sum(status(r)==s for r in group)
        out[mode] = dict(correct_total=len(correct), correct_supported=count(correct,'supported'),
            correct_conflict=count(correct,'conflict'), correct_inconclusive=count(correct,'inconclusive'),
            correct_not_materialized=count(correct,'not_materialized'), correct_crash=count(correct,'crash'),
            incorrect_total=len(wrong), incorrect_conflict=count(wrong,'conflict'),
            incorrect_supported=count(wrong,'supported'), incorrect_inconclusive=count(wrong,'inconclusive'),
            incorrect_not_materialized=count(wrong,'not_materialized'), incorrect_crash=count(wrong,'crash'))
    return out


def run(output, compiler='g++'):
    artifacts = Artifacts(output)
    started = time.monotonic()
    cases, duplicates = catalog()
    frozen = snapshot()
    protocol = dict(schema_version=1, source_files=frozen, widths=list(WIDTHS), families=list(FAMILIES),
                    default_budget=4096, exhaustive_budget=65536, primary_seed=PRIMARY_SEED,
                    extra_singleton_seeds=list(EXTRA_SEEDS), cases=cases, duplicate_case_ids=duplicates,
                    labeling='Independent native C++ exhaustive truth; no import of auditor rules',
                    metrics='Separate correct rejection, incorrect acceptance, inconclusive, and materialization failures',
                    limitations=['Same author, not independently human-reviewed or blind holdout.',
                                 'Synthetic functional families already known; new expressions/widths only.',
                                 'Rule bindings are provided, not generated by a model. No model capability measured.'])
    protocol['protocol_id'] = json_digest(protocol)
    artifacts.json('protocol.json', protocol)
    for name in frozen:
        artifacts.bytes('source/'+name, (ROOT/name).read_bytes())
    compiler_path = shutil.which(compiler)
    if not compiler_path: raise RuntimeError('A native C++ compiler is required for independent labels')
    native_source = artifacts.bytes('truth.cpp', truth_program(cases).encode())
    native_binary = artifacts.root/('truth.exe' if sys.platform=='win32' else 'truth')
    version = subprocess.run([compiler_path, '--version'], capture_output=True, text=True, timeout=30)
    command = [compiler_path, '-std=c++17', '-O2', str(native_source), '-o', str(native_binary)]
    build = subprocess.run(command, capture_output=True, text=True, timeout=180)
    artifacts.json('compiler.json', dict(command=command, version=version.stdout, platform=platform.platform(),
                                        exit_code=build.returncode, stdout=build.stdout, stderr=build.stderr))
    if build.returncode: raise RuntimeError('Independent label program failed to compile; see compiler.json')
    result = subprocess.run([str(native_binary)], capture_output=True, text=True, timeout=120)
    artifacts.bytes('truth.jsonl', result.stdout.encode())
    if result.returncode: raise RuntimeError('Independent label program failed')
    labels = {r['id']:r for r in map(json.loads, result.stdout.splitlines())}
    if set(labels) != {c['id'] for c in cases}: raise RuntimeError('Incomplete independent labels')
    records = []
    report = dict(protocol_id=protocol['protocol_id'], complete=False, model_requests=0, cases=records,
                  semantic_binding_challenges=[], applicability=[], helper_seed_runs=[])
    def persist():
        report['metrics'] = judge_counts(records)
        report['elapsed_seconds'] = round(time.monotonic()-started,3)
        artifacts.json('evaluation.json', report)
    def binding_for(generation, rule, width, task):
        return dict(schema_version=1, suite_id=generation['suite_id'], rule_id=rule, width=width,
                    evidence=[dict(source='problem', quote=task.problem)])
    def execute(suite, name, binding=None, budget=4096, seed=PRIMARY_SEED):
        path = None
        if binding is not None:
            path = artifacts.root/'bindings'/f'{name}.json'
            artifacts.json(f'bindings/{name}.json', binding)
        at = time.monotonic()
        try:
            raw = audit_suite(suite, artifacts.root/'audits'/name, binding=path, max_cases=budget, seed=seed)
            value = dict(status=raw['status'], checks=raw['checks'], errors=raw['errors'],
                         first_conflict=raw['conflicts'][0] if raw['conflicts'] else None,
                         oracle_evaluations=raw['oracle_evaluations'])
        except Exception as error:
            value = dict(status='crash', exception=type(error).__name__, message=str(error))
            artifacts.json(f'crashes/{name}.json', value)
        value['seconds'] = round(time.monotonic()-at, 6)
        return value
    for index, case in enumerate(cases):
        if snapshot() != frozen: raise RuntimeError('Source changed after freeze')
        truth = labels[case['id']]
        label = 'correct' if truth['mismatches']==0 else 'incorrect'
        if case['intended']=='mutation' and label=='correct': label='equivalent_mutation'
        if case['intended']=='correct' and label=='incorrect': label='fixture_error'
        row = dict(**case, label=label, truth=truth)
        records.append(row)
        if label in ('equivalent_mutation', 'fixture_error'):
            persist()
            continue
        task, response = task_and_response(case['rule'], case['width'], case['expression'])
        suite = artifacts.root/'suites'/case['id']
        generated = generate_suite(task, suite, response=response)
        row['materialization'] = generated
        if generated['status'] == 'generated_unreviewed':
            bound = binding_for(generated, case['rule'], case['width'], task)
            row['audits'] = {'default':execute(suite,case['id']+'_default',bound)}
            if case['width'] <= 12:
                row['audits']['exhaustive'] = dict(row['audits']['default'], reused_default_exhaustive=True)
            else:
                row['audits']['exhaustive'] = execute(suite,case['id']+'_exhaustive',bound,budget=65536)
                if case['singleton']:
                    for seed in EXTRA_SEEDS:
                        report['helper_seed_runs'].append(dict(id=case['id'], seed=seed,
                            result=execute(suite,f'{case["id"]}_seed{seed}',bound,seed=seed)))
        persist()
        print(f'{index+1}/{len(cases)} {case["id"]}: {row.get("audits",{}).get("default",{}).get("status",generated["status"])}', flush=True)
    # Semantic misbinding is a separate challenge, not mixed with correct-binding metrics.
    # All 6 ordered mismatches, both a correct-for-spec and wrong-for-spec oracle.
    for spec_rule, bound_rule in itertools.permutations(FAMILIES, 2):
        for matches_spec in (True, False):
            name = f'binding_{spec_rule}_as_{bound_rule}_{"correct" if matches_spec else "wrong"}'
            core = expression(spec_rule if matches_spec else bound_rule, 8)
            task, response = task_and_response(spec_rule, 8, core)
            suite = artifacts.root/'suites'/name
            generated = generate_suite(task, suite, response=response)
            if generated['status'] != 'generated_unreviewed':
                report['semantic_binding_challenges'].append(dict(id=name, materialization=generated))
                continue
            outcome = execute(suite, name, binding_for(generated, bound_rule, 8, task))
            report['semantic_binding_challenges'].append(dict(id=name, correct_for_spec=matches_spec,
                supplied_binding_is_wrong=True, result=outcome))
    scenarios = ('missing_binding','unknown_rule','wrong_width','partial_domain','signed_input','two_inputs',
                 'foreign_suite','invented_quote','budget_too_small','width17','binding_list','binding_null')
    for kind in scenarios:
        width = 17 if kind=='width17' else 8
        kwargs = {}
        if kind=='width17': kwargs['input_type']='uint32_t'
        if kind=='partial_domain': kwargs['domain']=[0,127]
        if kind=='signed_input': kwargs.update(input_type='int16_t',domain=[0,255])
        if kind=='two_inputs': kwargs['second_input']=True
        task, response = task_and_response('popcount',width,'0',**kwargs)
        suite = artifacts.root/'suites'/kind
        generated = generate_suite(task,suite,response=response)
        if generated['status'] != 'generated_unreviewed': raise RuntimeError('Applicability fixture failed: '+kind)
        bound = binding_for(generated,'popcount',width,task)
        if kind=='unknown_rule': bound['rule_id']='not_a_rule'
        if kind=='wrong_width': bound['width']=4
        if kind=='foreign_suite': bound['suite_id']='foreign'
        if kind=='invented_quote': bound['evidence'][0]['quote']='this sentence is not in the task'
        if kind=='binding_list': bound=[]
        if kind=='binding_null':
            # An explicit null JSON file differs from absent evidence.
            path=artifacts.root/'bindings'/'binding_null.json'
            artifacts.json('bindings/binding_null.json',None)
            try:
                result=audit_suite(suite,artifacts.root/'audits'/kind,binding=path)
                outcome=dict(status=result['status'],errors=result['errors'])
            except Exception as error:
                outcome=dict(status='crash',exception=type(error).__name__,message=str(error))
                artifacts.json(f'crashes/{kind}.json',outcome)
        else:
            outcome=execute(suite,kind,None if kind=='missing_binding' else bound,budget=1 if kind=='budget_too_small' else 4096)
        report['applicability'].append(dict(id=kind, expected='inconclusive', result=outcome))
        persist()
    if snapshot() != frozen: raise RuntimeError('Source changed during evaluation')
    report['complete']=True
    report['source_unchanged']=True
    report['label_summary']={label:sum(r['label']==label for r in records)
        for label in ('correct','incorrect','equivalent_mutation','fixture_error')}
    persist()
    print(json.dumps({k:v for k,v in report.items() if k not in ('cases','helper_seed_runs')},indent=2))
    return report


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output')
    parser.add_argument('--compiler',default='g++')
    parser.add_argument('--allow-execution',action='store_true')
    args=parser.parse_args()
    if not args.allow_execution: parser.error('Native truth labeling requires --allow-execution')
    run(args.output,args.compiler)
