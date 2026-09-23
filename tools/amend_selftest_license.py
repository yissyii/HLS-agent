"""Explicit license-path-only protocol amendment. Reuses exact generation bytes; no model calls."""
import argparse
import copy
from pathlib import Path
import shutil
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from agent.artifacts.writer import Artifacts
from agent.core.contracts import digest,json_digest
from agent.selftest.generate import load_suite
from agent.selftest.spec import require
from tools.run_selftest_study import read,check_protocol


def amend(dataset,generation,output,license_file):
    source=Path(generation).resolve()
    original=read(source/'protocol.json')
    check_protocol(original,dataset)
    summary=read(source/'generation_summary.json')
    require(summary['complete'] and summary['protocol_id']==original['protocol_id'],'Incomplete or mismatched generation')
    require(original['evaluation']['backend']=='vitis','Only a Vitis license correction is permitted')
    require(license_file.startswith('/') and '\n' not in license_file,'Require explicit absolute remote license path')
    frozen={row['id']:load_suite(source/'suites'/row['id'])[0]['suite_id'] for row in summary['tasks'] if row['status']=='generated_unreviewed'}
    revised=copy.deepcopy(original)
    old=revised['evaluation']['runtime']['hls']['license_file']
    require(old!=license_file,'License is unchanged')
    revised['evaluation']['runtime']['hls']['license_file']=license_file
    revised['amendment']=dict(previous_protocol_id=original['protocol_id'],kind='license_path_only',
        old_license_file=old,new_license_file=license_file,model_requests_added=0,
        generation_summary_sha256=digest((source/'generation_summary.json').read_bytes()),
        reason='Correct license path from an existing working toolchain configuration; no algorithm or model changes.')
    revised.pop('protocol_id')
    revised['protocol_id']=json_digest(revised)
    artifacts=Artifacts(output)
    artifacts.json('previous_protocol.json',original)
    artifacts.json('previous_generation_summary.json',summary)
    shutil.copytree(source/'suites',artifacts.root/'suites')
    summary=copy.deepcopy(summary);summary['protocol_id']=revised['protocol_id']
    artifacts.json('protocol.json',revised);artifacts.json('generation_summary.json',summary)
    for name,suite_id in frozen.items():
        require(load_suite(artifacts.root/'suites'/name)[0]['suite_id']==suite_id,'Copy changed generated suite')
    return revised['protocol_id']


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('dataset');parser.add_argument('generation');parser.add_argument('output');parser.add_argument('--license-file',required=True)
    args=parser.parse_args()
    print(amend(args.dataset,args.generation,args.output,args.license_file))
