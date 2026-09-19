"""Bounded Windows rename retry preserves atomic evidence writes."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from serve.inference import ROOT, write_json


@unittest.skipUnless(os.name == 'nt', 'Windows sharing semantics')
class ArtifactWrites(unittest.TestCase):
    def test_transient_lock_retries_only_rename(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'output') as directory:
            path = Path(directory)/'result.json'
            write_json(path, {'version': 1})
            original = Path.replace
            calls = []
            def replace(source, target):
                calls.append(target)
                if len(calls) == 1:
                    error = PermissionError('transient lock'); error.winerror = 5
                    raise error
                return original(source, target)
            with patch.object(Path, 'replace', replace), patch('serve.inference.time.sleep') as sleep:
                write_json(path, {'version': 2})
            self.assertEqual(json.loads(path.read_text()), {'version': 2})
            self.assertEqual(len(calls), 2)
            sleep.assert_called_once()

    def test_persistent_lock_fails_and_keeps_previous_receipt(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'output') as directory:
            path = Path(directory)/'result.json'
            write_json(path, {'version': 1})
            error = PermissionError('persistent lock'); error.winerror = 5
            with patch.object(Path, 'replace', side_effect=error) as replace, patch('serve.inference.time.sleep'):
                with self.assertRaises(PermissionError): write_json(path, {'version': 2})
            self.assertEqual(replace.call_count, 6)
            self.assertEqual(json.loads(path.read_text()), {'version': 1})


if __name__ == '__main__':
    unittest.main()
