import copy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from serve.baseline_entry import run
from serve import paired_entry


class BaselineContract(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT/'output')
        self.directory = Path(self.temp.name)
        self.problem = self.directory/'problem.txt'
        self.problem.write_bytes('题目原文\r\n  preserve whitespace\n'.encode('utf-8'))
        self.requests = []
        self.mode = 'stop'
        owner = self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args): pass
            def do_POST(self):
                request = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                owner.requests.append((self.path,request))
                if owner.mode == 'disconnect':
                    self.connection.shutdown(socket.SHUT_RDWR)
                    self.connection.close()
                    return
                payload = dict(choices=[dict(message=dict(content='int kernel() { return 0; }\n'),finish_reason=owner.mode)],
                               usage=dict(prompt_tokens=10,completion_tokens=10,total_tokens=20))
                data = json.dumps(payload).encode()
                self.send_response(200)
                self.send_header('Content-Length',str(len(data)))
                self.end_headers()
                self.wfile.write(data)
        self.server = ThreadingHTTPServer(('127.0.0.1',0),Handler)
        self.thread = threading.Thread(target=self.server.serve_forever,daemon=True)
        self.thread.start()
        self.config = json.loads((ROOT/'serve/runtime.json').read_text())
        self.config['model']['base_url'] = 'http://127.0.0.1:'+str(self.server.server_port)+'/v1'
    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.temp.cleanup()
    def test_problem_only_single_request_and_no_overwrite(self):
        output=self.directory/'baseline'
        code,result=run(self.problem,output,self.config,'test')
        self.assertEqual(code,0)
        self.assertEqual(len(self.requests),1)
        path,payload=self.requests[0]
        self.assertEqual(path,'/v1/chat/completions')
        self.assertEqual(payload['messages'],[dict(role='user',content=self.problem.read_bytes().decode('utf-8'))])
        self.assertNotIn('tools',payload)
        self.assertEqual(result['generation_requests'],1)
        self.assertEqual(result['tool_calls'],0)
        self.assertEqual((output/'problem.txt').read_bytes(),self.problem.read_bytes())
        with self.assertRaises(FileExistsError):run(self.problem,output,self.config,'test')
        self.assertEqual(len(self.requests),1)
    def test_truncation_preserved_without_retry_or_candidate(self):
        self.mode='length'
        output=self.directory/'baseline'
        code,result=run(self.problem,output,self.config,'test')
        self.assertEqual(code,1)
        self.assertEqual(result['category'],'generation_incomplete')
        self.assertTrue((output/'response.txt').is_file())
        self.assertFalse((output/'candidate.cpp').exists())
        self.assertEqual(len(self.requests),1)
    def test_network_failure_is_not_retried(self):
        self.mode='disconnect'
        code,result=run(self.problem,self.directory/'baseline',self.config,'test')
        self.assertEqual(code,1)
        self.assertEqual(result['category'],'api_network_or_timeout')
        self.assertEqual(len(self.requests),1)
    def test_pair_requires_agent_before_model_call(self):
        with patch.object(sys,'argv',['paired',str(self.problem),str(self.directory/'pair'),'--agent-entry',str(self.directory/'missing.sh')]):
            with self.assertRaises(SystemExit):paired_entry.main()
        self.assertEqual(self.requests,[])
    def test_pair_shares_problem_config_and_run_id(self):
        agent=self.directory/'agent.sh'
        agent.write_text('# test fixture only\n')
        out=self.directory/'pair'
        def fake_agent(command,**kwargs):
            problem,output=command[2:4]
            config=json.loads(Path(command[5]).read_text())
            code,receipt=run(problem,output,config,command[7])
            from agent.core.policy import load_policy
            from agent.context.skills import Skills
            from serve.baseline_entry import config_digest
            receipt.update(policy_sha256=config_digest(load_policy()), skills_sha256=Skills().sha256)
            (Path(output)/'result.json').write_text(json.dumps(receipt), encoding='utf-8')
            return {'exit_code': code, 'timed_out': False, 'elapsed_seconds': 0, 'log': 'agent.log'}
        def fake_process(command, work, environment, log, timeout):
            return fake_agent(command)
        argv=['paired',str(self.problem),str(out),'--agent-entry',str(agent)]
        with patch.object(sys,'argv',argv),patch.object(paired_entry,'load_config',return_value=copy.deepcopy(self.config)),patch.object(paired_entry,'run_process',side_effect=fake_process):
            self.assertEqual(paired_entry.main(),0)
        pair=json.loads((out/'attempt_000/result/pair.json').read_text())
        self.assertTrue(pair['pairing_verified'])
        self.assertEqual(len(self.requests),2)
        self.assertEqual(self.requests[0],self.requests[1])


if __name__=='__main__':
    unittest.main()
