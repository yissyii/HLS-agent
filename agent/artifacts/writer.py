"""All mutable evidence stays in an exclusive run directory."""
from datetime import datetime, timezone
import json
from pathlib import Path

from serve.inference import write_json


class Artifacts:
    def __init__(self, output):
        self.root = Path(output).resolve()
        self.root.mkdir(parents=True, exist_ok=False)

    def json(self, name, value):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        write_json(path, value)

    def bytes(self, name, value):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(value)
        return path

    def event(self, kind, **fields):
        value = dict(time=datetime.now(timezone.utc).isoformat(), event=kind, **fields)
        with (self.root / 'events.jsonl').open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(value, ensure_ascii=False) + '\n')
            stream.flush()

    def attempt(self, number):
        path = self.root / 'candidates' / f'{number:03d}'
        path.mkdir(parents=True, exist_ok=False)
        return path
