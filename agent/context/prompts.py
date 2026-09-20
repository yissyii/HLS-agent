"""Versioned prompt text, loaded once and frozen for an Agent run."""
from dataclasses import dataclass
import json
from pathlib import Path

from agent.core.contracts import digest, json_digest
from agent.toolchain import VITIS_VERSION
from serve.inference import Failure

PROMPT_ROOT = Path(__file__).resolve().parents[1] / 'prompts'
PROMPT_FILES = ('manifest.json', 'system.txt', 'initial.txt', 'repair.txt')


@dataclass(frozen=True)
class PromptTemplates:
    version: str
    system: str
    initial: str
    repair: str

    def snapshot(self):
        return dict(schema_version=1, id='vitis-hls-cpp', version=self.version,
                    tool_version=VITIS_VERSION, files={
                        name + '.txt': {'text': text, 'sha256': digest(text.encode('utf-8'))}
                        for name, text in (('system', self.system), ('initial', self.initial), ('repair', self.repair))})

    @property
    def sha256(self):
        return json_digest(self.snapshot())


def load_prompts(directory=None):
    root = Path(directory) if directory is not None else PROMPT_ROOT
    try:
        manifest = json.loads((root / 'manifest.json').read_text(encoding='utf-8'))
        if (not isinstance(manifest, dict)
                or set(manifest) != {'schema_version', 'id', 'version', 'tool_version'}
                or type(manifest['schema_version']) is not int or manifest['schema_version'] != 1
                or manifest['id'] != 'vitis-hls-cpp' or manifest['tool_version'] != VITIS_VERSION
                or not isinstance(manifest['version'], str) or not manifest['version'].strip()):
            raise ValueError('Invalid prompt manifest or Vitis version')
        texts = {}
        for name in ('system', 'initial', 'repair'):
            # Normalize line endings so a Windows checkout and Linux checkout agree.
            text = (root / (name + '.txt')).read_text(encoding='utf-8')
            if not text.strip():
                raise ValueError('Empty prompt: ' + name)
            texts[name] = text
        return PromptTemplates(version=manifest['version'], **texts)
    except (OSError, ValueError, TypeError) as error:
        raise Failure('prompt_configuration_error', 'Cannot load prompt templates: ' + str(error)) from error
