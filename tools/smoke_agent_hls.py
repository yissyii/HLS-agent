"""Real Vitis smoke test with a synthetic task and a loopback model fixture.

No remote model request or frozen dataset is used. Evidence remains in output.
"""
import argparse
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sys
import threading

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent.interface.entry import run
from serve.inference import load_config, write_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', default='serve/runtime.local.json')
    args = parser.parse_args()
    runtime = load_config(args.config, frozen=True)
    task = ROOT / 'output' / ('agent_hls_smoke_' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
    task.mkdir(parents=True)
    problem = task / 'problem.txt'
    problem.write_text('Implement int kernel(int a) returning a + 1. All inputs are integers in [-100, 100].\n', encoding='utf-8')
    (task / 'api.h').write_text('int kernel(int a);\n', encoding='utf-8')
    (task / 'tb.cpp').write_text('#include "api.h"\nint main(){for(int a=-100;a<=100;++a){if(kernel(a)!=a+1)return 1;}return 0;}\n', encoding='utf-8')
    manifest = task / 'task.json'
    write_json(manifest, dict(id='synthetic_increment_smoke', top_function='kernel', problem_file='problem.txt',
                             source_file='kernel.cpp', testbench_files=['tb.cpp'], support_files=['api.h'],
                             model_visible=['api.h'], feedback_policy='public_diagnostics'))
    requests = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            requests.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
            source = ('int kernel(int a){return missing_identifier;}\n' if len(requests) == 1
                      else 'int kernel(int a){return a+1;}\n')
            payload = json.dumps({'choices': [{'message': {'content': source}, 'finish_reason': 'stop'}]}).encode()
            self.send_response(200)
            self.send_header('Content-Length', str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    runtime['model']['base_url'] = f'http://127.0.0.1:{server.server_port}/v1'
    try:
        code, result = run(problem, task / 'agent', config=runtime, manifest=manifest, cpu_only=True)
        write_json(task / 'fixture.json', {'synthetic_task': True, 'mock_model': True,
                                          'real_vitis': True, 'requests': requests})
        passed = code == 0 and result['status'] == 'passed' and len(requests) == 2 and result['tool_calls'] == 3
        print(json.dumps({'passed': passed, 'output': str(task), 'status': result['status'],
                          'stop_reason': result['stop_reason'], 'requests': len(requests),
                          'tool_calls': result['tool_calls'], 'elapsed_seconds': result['elapsed_seconds']}))
        return 0 if passed else 1
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


if __name__ == '__main__':
    raise SystemExit(main())
