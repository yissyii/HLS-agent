"""Author-created 20-task internal pilot; NOT an independently authored blind set."""
import argparse
from pathlib import Path
import random
import shutil
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from agent.artifacts.writer import Artifacts
from agent.core.contracts import digest,json_digest
from agent.selftest.spec import PublicTask


def cases():
    rows=[]
    templates={
        'gray_decode': [
            'A sensor transmits reflected Gray words. Recover the original unsigned binary number.',
            'Return n such that the supplied x equals n XOR floor(n/2); this is the inverse conversion, not encoding.',
            'Decode a reflected Gray representation into ordinary binary. The leading output bit copies the leading input bit; every following output bit is the preceding output bit XOR the corresponding input bit.',
            'The input is a Gray-coded position, obtained by applying g=n XOR (n shifted right once). Return the position n rather than another Gray word.'],
        'reverse_bits': [
            'Mirror the bit positions of the complete declared word, keeping leading zero positions.',
            'Reverse the order of the declared bits: output position i receives input position W-1-i. This is not a byte swap.',
            'A fixed-width bit string is read right to left and returned as an unsigned number. Leading zeros participate in the reversal.',
            'Reflect all positions in the declared W-bit word. For example, a single set bit at index i moves to index W-1-i; no other transformation is performed.'],
        'popcount': [
            'Return the total number of one bits in the input word, not its parity.',
            'Count the occupied positions in the binary representation: each bit equal to one contributes exactly one to the result.',
            'Return the Hamming weight of the entire declared unsigned word; zeros contribute nothing.',
            'The output is the number of asserted bits across all W positions, including the highest position. Do not count leading zeros.']}
    kinds={'gray_decode':0,'reverse_bits':1,'popcount':2}
    for rule in templates:
        for i,width in enumerate((3,5,7,10)):
            problem=f'Stateless transform_word. W={width}. {templates[rule][i]} Legal input x is any integer from 0 through {(1<<width)-1}, inclusive. Higher storage bits are zero. Return the result as uint16_t.'
            rows.append(dict(problem=problem,signature='uint16_t transform_word(uint16_t x);',group='applicable',
                expected_decision='propose',rule_id=rule,width=width,domains=[[0,(1<<width)-1]],names=['x'],
                kind=kinds[rule],oracle_evaluable=True,reason='Exact full-domain unsigned stateless rule.'))
    def add(problem,group,decision,kind,domains,*,signature='uint16_t transform_word(uint16_t x);',names=None,reason=''):
        rows.append(dict(problem=problem,signature=signature,group=group,expected_decision=decision,
            rule_id=None,width=None,domains=domains,names=names or ['x'],kind=kind,
            oracle_evaluable=kind>=0,reason=reason))
    add('Stateless transform_word encodes an unsigned binary number x into reflected Gray code: return x XOR floor(x/2). Every x from 0 through 255 is legal. This is the forward conversion, not decoding.',
        'confusable','unsupported',3,[[0,255]],reason='Encoding is not the registered decoding rule.')
    add('Stateless transform_word swaps the high and low bytes of a 16-bit unsigned word. Preserve the order of bits inside each byte. Every x from 0 through 65535 is legal.',
        'confusable','unsupported',4,[[0,65535]],reason='Byte exchange is not bit reversal.')
    add('Stateless transform_word reverses only bits 0 through 7 of x while preserving bits 8 through 15 in their original positions. Every x from 0 through 65535 is legal. Return the complete resulting 16-bit word.',
        'confusable','unsupported',5,[[0,65535]],reason='Partial reversal on a full 16-bit domain is not any registered full-word rule.')
    add('Stateless transform_word should reverse the bits of x. Every x from 0 through 65535 is legal. The specification does not resolve whether to reverse all sixteen bits or only the low eight bits while preserving the upper byte. Ask for clarification; neither interpretation has priority.',
        'confusable','uncertain',-1,[[0,65535]],reason='Two explicitly unresolved behaviors; no unique oracle.')
    add('Stateless transform_word returns the parity of the number of one bits in x: return 1 for an odd count and 0 for an even count. Every x from 0 through 255 is legal.',
        'unsupported','unsupported',6,[[0,255]],reason='Parity is not population count.')
    add('Stateless transform_word takes two unsigned values x and y, each from 0 through 31 inclusive. Return x+y if the sum is at most 31; otherwise return 31. This is saturating addition, not modular arithmetic.',
        'unsupported','unsupported',7,[[0,31],[0,31]],signature='uint16_t transform_word(uint16_t x, uint16_t y);',names=['x','y'],reason='Two-input saturating arithmetic is outside the registry.')
    add('Stateless transform_word accepts signed x from -128 through 127 inclusive. Return the number of ones in its eight-bit two\'s-complement representation, including the sign bit. Negative inputs are legal and must not be discarded.',
        'unsupported','unsupported',8,[[-128,127]],signature='uint16_t transform_word(int16_t x);',reason='Mathematical count is meaningful but registry requires an unsigned input.')
    add('Stateful transform_word maintains an accumulator initialized to zero before the first call. x ranges from 0 through 31 and reset is either 0 or 1. If reset=1, set the accumulator to zero and ignore x; otherwise add x modulo 256. Return the updated accumulator. State persists across calls.',
        'unsupported','unsupported',-1,[[0,31],[0,1]],signature='uint16_t transform_word(uint16_t x, uint16_t reset);',names=['x','reset'],reason='Stateful sequences are outside scalar stateless generation and rule scope.')
    random.Random(20260924).shuffle(rows)
    return [dict(row,id=f'case{i:02d}') for i,row in enumerate(rows,1)]


def truth_source(rows):
    entries=[]
    for r in rows:
        if r['oracle_evaluable']:
            lo,hi=r['domains'][0]; lo2,hi2=r['domains'][1] if len(r['domains'])==2 else (0,0)
            entries.append(f'{{"{r["id"]}",{r["kind"]},{r["width"] or 0},{lo},{hi},{lo2},{hi2}}}')
    return '''#include <iostream>
#include <stdexcept>
unsigned rev(unsigned x,unsigned w){unsigned y=0;while(w--){y=2*y+x%2;x/=2;}return y;}
unsigned count(unsigned x){unsigned y=0;while(x){y+=x%2;x/=2;}return y;}
struct T{const char* id;int kind,w,lo,hi,lo2,hi2;};
T tasks[]={'''+','.join(entries)+'''};
int main(){for(auto t:tasks){for(int x=t.lo;x<=t.hi;++x){for(int y=t.lo2;y<=t.hi2;++y){
unsigned z=0;
switch(t.kind){
case 0: {bool found=false;for(unsigned n=0;n<(1u<<t.w);++n){if((n^(n/2))==unsigned(x)){z=n;found=true;break;}}if(!found)throw std::runtime_error("gray inverse");break;}
case 1:z=rev(x,t.w);break;
case 2:z=count(x);break;
case 3:z=unsigned(x)^(unsigned(x)/2);break;
case 4:z=(x%256)*256+x/256;break;
case 5:z=(x/256)*256+rev(x%256,8);break;
case 6:z=count(x)%2;break;
case 7:z=(x+y>31)?31:x+y;break;
case 8:z=count((x%256+256)%256);break;
default:throw std::runtime_error("unknown kind");}
std::cout<<t.id<<'\\t'<<x<<'\\t'<<y<<'\\t'<<z<<'\\n';
}}}}\n'''


def prepare(output,compiler='g++'):
    artifacts=Artifacts(output)
    rows=cases(); public=[]
    for row in rows:
        task=PublicTask(row['problem'],'#include <stdint.h>\n'+row['signature']+'\n')
        task.validate()
        artifacts.bytes(f'public/{row["id"]}/problem.txt',task.problem.encode())
        artifacts.bytes(f'public/{row["id"]}/interface.h',task.interface.encode())
        public.append(dict(id=row['id'],**task.snapshot()))
    source=artifacts.bytes('private/truth.cpp',truth_source(rows).encode())
    executable=source.with_suffix('.exe' if sys.platform=='win32' else '.bin')
    executable_compiler=shutil.which(compiler)
    if executable_compiler is None: raise RuntimeError('C++ compiler required')
    build=subprocess.run([executable_compiler,'-std=c++17','-O2',str(source),'-o',str(executable)],capture_output=True,text=True,timeout=120)
    artifacts.json('private/compiler.json',dict(exit_code=build.returncode,stdout=build.stdout,stderr=build.stderr,
        version=subprocess.run([executable_compiler,'--version'],capture_output=True,text=True,timeout=30).stdout))
    if build.returncode: raise RuntimeError('Reference compiler failed')
    result=subprocess.run([str(executable)],capture_output=True,timeout=120)
    if result.returncode: raise RuntimeError('Reference execution failed')
    artifacts.bytes('private/truth.tsv',result.stdout)
    fixtures=dict(tasks=rows,truth_sha256=digest(result.stdout),private_canary='PRIVATE_REFERENCE_DO_NOT_SEND_v1',
                  limitation='Same author as implementation; independent human review pending. Not official or training-excluded tasks.')
    artifacts.json('private/fixtures.json',fixtures)
    manifest=dict(schema_version=1,tasks=public,private_files={p.name:digest(p.read_bytes()) for p in sorted((artifacts.root/'private').iterdir()) if p.is_file()})
    manifest['dataset_id']=json_digest(manifest)
    artifacts.json('public_manifest.json',manifest)
    print(f'Prepared {len(rows)} tasks; independent C++ truth table has {len(result.stdout.splitlines())} rows',flush=True)
    return artifacts.root


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('output');p.add_argument('--compiler',default='g++');p.add_argument('--allow-execution',action='store_true')
    a=p.parse_args()
    if not a.allow_execution:p.error('Reference C++ execution requires --allow-execution')
    prepare(a.output,a.compiler)
