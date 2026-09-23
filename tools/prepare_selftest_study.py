"""Original family-separated pilot set. Evaluation fixtures are never model inputs."""
import argparse
import copy
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent.artifacts.writer import Artifacts
from agent.core.contracts import digest, json_digest
from agent.selftest.generate import generate_suite
from agent.selftest.spec import PublicTask
from tools.prepare_selftest_examples import EXAMPLES, response_for


HOLDOUT = [
    dict(id='count_bits8', family='population_count', output='uint8_t', inputs=[('x', 'uint8_t', 0, 255)],
         problem='Stateless function count_bits8 returns the number of one bits in the eight-bit binary representation of x. Every uint8_t input is legal.',
         oracle='+'.join(f'((x>>{i})&1)' for i in range(8)),
         correct='unsigned n=0; while(x){x=uint8_t(x&(x-1)); ++n;} return n;',
         mutants={'ignore_msb':'unsigned n=0; x&=127; while(x){x=uint8_t(x&(x-1)); ++n;} return n;',
                  'parity':'unsigned n=0; while(x){n^=x&1; x>>=1;} return n;',
                  'rare181':'if(x==181)return 0; unsigned n=0; while(x){n+=x&1; x>>=1;} return n;'}, exhaustive=True),
    dict(id='leading_zeros8', family='leading_zero_count', output='uint8_t', inputs=[('x', 'uint8_t', 0, 255)],
         problem='Stateless function leading_zeros8 counts zero bits from bit 7 downward until the first one bit in x. Return 8 when x is zero. Every uint8_t input is legal.',
         oracle='0 if x>=128 else 1 if x>=64 else 2 if x>=32 else 3 if x>=16 else 4 if x>=8 else 5 if x>=4 else 6 if x>=2 else 7 if x>=1 else 8',
         correct='unsigned n=0; for(int i=7;i>=0;--i){if(x&(1u<<i))break; ++n;} return n;',
         mutants={'zero_case':'if(!x)return 0; unsigned n=0; while(!(x&128)){++n;x=uint8_t(x<<1);} return n;',
                  'trailing':'if(!x)return 8; unsigned n=0; while(!(x&1)){++n;x>>=1;} return n;',
                  'boundary128':'if(x==128)return 1; unsigned n=0; for(int i=7;i>=0;--i){if(x&(1u<<i))break;++n;} return n;'}, exhaustive=True),
    dict(id='reverse_bits8', family='bit_reversal', output='uint8_t', inputs=[('x', 'uint8_t', 0, 255)],
         problem='Stateless function reverse_bits8 reverses the order of all eight bits of x: input bit i becomes output bit 7-i, for i=0 through 7. Every uint8_t input is legal.',
         oracle=' | '.join(f'(((x>>{i})&1)<<{7-i})' for i in range(8)),
         correct='unsigned y=0; for(unsigned i=0;i<8;++i){y=y*2+(x%2); x/=2;} return y;',
         mutants={'identity':'return x;', 'seven_bits':'unsigned y=0; for(unsigned i=0;i<7;++i){y=(y<<1)|(x&1);x>>=1;}return y;',
                  'rare181':'if(x==181)return 0; unsigned y=0;for(unsigned i=0;i<8;++i){y=(y<<1)|(x&1);x>>=1;}return y;'}, exhaustive=True),
    dict(id='decode_bcd8', family='decimal_encoding', output='uint8_t', inputs=[('x', 'uint8_t', 0, 255)],
         problem='Stateless function decode_bcd8 interprets the high nibble of x as a decimal tens digit and the low nibble as a decimal ones digit. If either digit exceeds 9, return 255. Otherwise return ten times the tens digit plus the ones digit. Every uint8_t input is legal, including invalid encodings.',
         oracle='255 if (x>>4)>9 or (x&15)>9 else (x>>4)*10+(x&15)',
         correct='unsigned tens=x/16, ones=x%16; if(tens>=10||ones>=10)return 255; return tens*10+ones;',
         mutants={'no_validation':'return (x>>4)*10+(x&15);', 'swap_digits':'unsigned a=x>>4,b=x&15;if(a>9||b>9)return 255;return b*10+a;',
                  'reject9':'unsigned a=x>>4,b=x&15;if(a>=9||b>=9)return 255;return a*10+b;'}, exhaustive=True),
    dict(id='gray_decode8', family='gray_encoding', output='uint8_t', inputs=[('x', 'uint8_t', 0, 255)],
         problem='Stateless function gray_decode8 converts an eight-bit reflected Gray-code word x to its unsigned binary value. Output bit 7 equals input bit 7. For i from 6 down to 0, output bit i equals output bit i+1 XOR input bit i. Every uint8_t input is legal.',
         oracle=' ^ '.join(f'(x>>{i})' for i in range(8)),
         correct='unsigned y=0; for(unsigned p=x;p;p>>=1)y^=p; return y;',
         mutants={'encode_instead':'return x^(x>>1);', 'omit_msb_term':'unsigned y=0;for(unsigned i=0;i<7;++i)y^=x>>i;return y;',
                  'rare181':'if(x==181)return 0; unsigned y=x; y^=y>>1; y^=y>>2; y^=y>>4; return y;'}, exhaustive=True),
]


def prepare(output):
    artifacts = Artifacts(output)
    dev = [copy.deepcopy(x) for x in EXAMPLES if x['split'] == 'development']
    for item in dev:
        item['family'] = {'add8':'wrapping_addition','less_signed':'signed_comparison','mux4':'multiplexer',
                          'xor_mask':'constant_xor','sat_add':'saturating_addition'}[item['id']]
    heldout = [dict(copy.deepcopy(x), split='holdout') for x in HOLDOUT]
    public, private = [], []
    for item in dev + heldout:
        name = item['id']
        declaration = item['output'] + ' ' + name + '(' + ', '.join(t+' '+n for n,t,_,_ in item['inputs']) + ')'
        header = '#pragma once\n#include <stdint.h>\n' + declaration + ';\n'
        task = PublicTask(item['problem']+'\n', header)
        artifacts.bytes(f'public/{name}/problem.txt', task.problem.encode())
        artifacts.bytes(f'public/{name}/interface.h', header.encode())
        public.append(dict(id=name, split=item['split'], family=item['family'], **task.snapshot()))
        variants = []
        for variant, body in {'correct':item['correct'], **item['mutants']}.items():
            content = ('#include "interface.h"\n'+declaration+' { '+body+' }\n').encode()
            path = f'private/{name}/{variant}.cpp'
            artifacts.bytes(path, content)
            variants.append(dict(id=variant,role='correct' if variant=='correct' else 'mutant',path=path,sha256=digest(content)))
        private.append(dict(id=name, split=item['split'], variants=variants))
        result = generate_suite(task, artifacts.root/'gold_suites'/name, response=response_for(item))
        if result['status'] != 'generated_unreviewed':
            raise ValueError(result)
    publication = dict(schema_version=1, tasks=public)
    publication['dataset_id'] = json_digest(publication)
    artifacts.json('public_manifest.json', publication)
    artifacts.json('fixtures.json', dict(schema_version=1, dataset_id=publication['dataset_id'], tasks=private))
    artifacts.json('study_notes.json', dict(scope='Original scalar synthetic pilot; not official competition data',
        holdout_policy='New families; never tuned using this holdout. After inspection, retire as fresh holdout.',
        limitations=['Author knows both partitions; independent team review still needed.',
                     'No guarantee that task concepts are absent from model training.',
                     'Gold suites are authored controls, not formally proven specifications.']))
    return artifacts.root


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output')
    print(prepare(parser.parse_args().output))
