"""Materialize ORIGINAL synthetic development fixtures, never official benchmark answers.

Recorded plans are hand-authored fixtures, NOT evidence of model generation quality.
Public files, replay responses and private evaluation implementations live separately.
"""
import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent.artifacts.writer import Artifacts
from agent.selftest.generate import generate_suite
from agent.selftest.spec import PublicTask


EXAMPLES = [
    dict(id='add8', split='development', output='uint8_t', inputs=[('a', 'uint8_t', 0, 255), ('b', 'uint8_t', 0, 255)],
         problem='Stateless function add8 returns (a + b) modulo 256 for all uint8_t inputs.',
         oracle='(a + b) & 255', correct='return static_cast<uint8_t>(unsigned(a) + unsigned(b));',
         mutants={'subtract': 'return a-b;', 'truncate7': 'return (a+b)&127;', 'saturate': 'return a+b>255?255:a+b;'}),
    dict(id='less_signed', split='development', output='bool', inputs=[('a', 'int8_t', -128, 127), ('b', 'int8_t', -128, 127)],
         problem='Stateless function less_signed returns true exactly when signed input a is strictly less than signed input b. All int8_t values are legal.',
         oracle='a < b', correct='return a < b;',
         mutants={'unsigned_compare': 'return uint8_t(a)<uint8_t(b);', 'not_strict': 'return a<=b;', 'reversed': 'return a>b;'}),
    dict(id='mux4', split='development', output='uint8_t', inputs=[('a', 'uint8_t', 0, 15), ('b', 'uint8_t', 0, 15), ('sel', 'bool', 0, 1)],
         problem='Stateless function mux4 accepts a and b in [0,15]. It returns b when sel is true, otherwise a. Both bool values of sel are legal.',
         oracle='b if sel else a', correct='return sel ? b : a;',
         mutants={'swapped': 'return sel?a:b;', 'ignore_sel': 'return a;', 'truncate3': 'return (sel?b:a)&7;'}, exhaustive=True),
    dict(id='xor_mask', split='development', output='uint8_t', inputs=[('x', 'uint8_t', 0, 255)],
         problem='Stateless function xor_mask returns the bitwise exclusive OR of x with hexadecimal A5. All uint8_t inputs are legal.',
         oracle='x ^ 165', correct='return x ^ 0xA5;',
         mutants={'or': 'return x|0xA5;', 'and': 'return x&0xA5;', 'wrong_mask': 'return x^0x5A;'}, exhaustive=True),
    dict(id='sat_add', split='development', output='uint8_t', inputs=[('a', 'uint8_t', 0, 255), ('b', 'uint8_t', 0, 255)],
         problem='Stateless function sat_add returns the smaller of the mathematical sum a+b and 255. All uint8_t inputs are legal.',
         oracle='255 if a+b>255 else a+b', correct='unsigned s=unsigned(a)+unsigned(b); return s>255?255:s;',
         mutants={'wrap': 'return a+b;', 'cap254': 'return a+b>254?254:a+b;', 'maximum': 'return a>b?a:b;'}),
    dict(id='rotate4', split='reserved', output='uint8_t', inputs=[('x', 'uint8_t', 0, 15), ('s', 'uint8_t', 0, 3)],
         problem='Stateless function rotate4 accepts x in [0,15] and s in [0,3]. Rotate the four-bit value x left by s positions, wrapping shifted-out bits into the low positions. Return an integer in [0,15].',
         oracle='((x << s) | (x >> (4-s))) & 15',
         correct='unsigned r=x; for(unsigned i=0;i<s;++i) r=((r*2)&15)|(r/8); return r;',
         mutants={'shift_only': 'return (x<<s)&15;', 'rotate_right': 'return ((x>>s)|(x<<(4-s)))&15;', 'identity': 'return x;'}, exhaustive=True),
    dict(id='multiply4', split='reserved', output='uint8_t', inputs=[('a', 'uint8_t', 0, 15), ('b', 'uint8_t', 0, 15)],
         problem='Stateless function multiply4 accepts a and b in [0,15]. Return their exact unsigned mathematical product, which fits in uint8_t.',
         oracle='a*b', correct='unsigned sum=0; for(unsigned i=0;i<b;++i) sum+=a; return sum;',
         mutants={'add': 'return a+b;', 'truncate4': 'return (a*b)&15;', 'off_by_one': 'return a*b+1;'}, exhaustive=True),
]


def response_for(item):
    quote = [{'source': 'problem', 'quote': item['problem']}]
    contract = dict(schema_version=1, top_function=item['id'], state='stateless',
        inputs=[dict(name=n, type=t, domain=[lo, hi], evidence=quote) for n, t, lo, hi in item['inputs']],
        output_type=item['output'], rules=[dict(id='R1', description=item['problem'], evidence=quote)], uncertainties=[])
    plan = dict(schema_version=1, oracle=dict(expression=item['oracle'], rule_ids=['R1']), explicit_cases=[],
                sampling=dict(mode='exhaustive' if item.get('exhaustive') else 'boundary_random', seed=20260922,
                              random_cases=0 if item.get('exhaustive') else 128))
    return dict(contract=contract, test_plan=plan)


def prepare(output):
    artifacts = Artifacts(output)
    tasks = []
    for item in EXAMPLES:
        name = item['id']
        declaration = item['output'] + ' ' + name + '(' + ', '.join(t + ' ' + n for n, t, _, _ in item['inputs']) + ')'
        header = '#pragma once\n#include <stdint.h>\n' + declaration + ';\n'
        task = PublicTask(item['problem'] + '\n', header)
        artifacts.bytes(f'public/{name}/problem.txt', task.problem.encode())
        artifacts.bytes(f'public/{name}/interface.h', header.encode())
        response = response_for(item)
        artifacts.json(f'recorded_responses/{name}.json', response)
        variants = {'correct': item['correct'], **item['mutants']}
        paths = []
        for variant, body in variants.items():
            path = f'private/{name}/{variant}.cpp'
            artifacts.bytes(path, ('#include "interface.h"\n' + declaration + ' { ' + body + ' }\n').encode())
            paths.append(dict(id=variant, role='correct' if variant == 'correct' else 'mutant', path=path))
        generated = generate_suite(task, artifacts.root / 'suites' / name, response=response)
        if generated['status'] != 'generated_unreviewed':
            raise ValueError(generated)
        tasks.append(dict(id=name, split=item['split'], variants=paths))
    manifest = dict(schema_version=1, origin='hand_authored_synthetic_fixtures',
                    warning='Reserved examples are smoke fixtures, not an unseen-model held-out efficacy result.', tasks=tasks)
    artifacts.json('fixtures.json', manifest)
    return artifacts.root


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output')
    print(prepare(parser.parse_args().output))
