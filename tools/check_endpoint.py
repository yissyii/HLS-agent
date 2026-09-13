"""Check the configured endpoint with the user's minimal request, no retries."""
import hashlib
import http.client
import json
from pathlib import Path
import ssl
import urllib.parse

root = Path(__file__).resolve().parents[1]
config = json.loads((root / "serve/runtime.json").read_text())['model']
url = urllib.parse.urlsplit(config['base_url'])
context = ssl._create_unverified_context() if config['tls_sha256'] else ssl.create_default_context()
conn = http.client.HTTPSConnection(url.hostname, url.port, context=context, timeout=60)
conn.connect()
if config['tls_sha256']:
    assert hashlib.sha256(conn.sock.getpeercert(binary_form=True)).hexdigest() == config['tls_sha256'], 'Certificate changed'
body = {'model': config['name'], 'messages': [{'role':'user','content':'9.9和9.11哪个大？'}], 'max_tokens':2048,'temperature':0.7}
conn.request('POST',url.path+'/chat/completions',json.dumps(body).encode(),{'Content-Type':'application/json','Authorization':'Bearer not-needed'})
response = conn.getresponse()
print('HTTP', response.status)
print(response.read().decode('utf-8', errors='replace')[:4000])
conn.close()
raise SystemExit(0 if response.status == 200 else 1)
