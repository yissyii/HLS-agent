'''Read an existing batch without modifying original evidence.'''
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from evaluation.metrics import summarize

if __name__ == '__main__':
    batch = json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
    print(json.dumps(summarize(batch['results']), indent=2, ensure_ascii=False))
