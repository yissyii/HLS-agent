"""Canonical files, hashes and validation shared by offline RAG tools."""
import hashlib
import json
from pathlib import Path


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def file_sha256(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(block)
    return value.hexdigest()


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def write_corpus(directory, records, metadata):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=False)
    ids = [r['id'] for r in records]
    if len(ids) != len(set(ids)) or not ids:
        raise ValueError('Corpus must contain unique, nonempty records')
    for r in records:
        validate_record(r)
    payload = b''.join(canonical(r) + b'\n' for r in records)
    (directory / 'records.jsonl').write_bytes(payload)
    manifest = dict(schema_version=2, records=len(records), records_sha256=sha256(payload), **metadata)
    write_json(directory / 'manifest.json', manifest)
    return manifest


def load_corpus(directory):
    directory = Path(directory)
    manifest = read_json(directory / 'manifest.json')
    if manifest.get('schema_version') != 2:
        raise ValueError('Corpus schema v1 is no longer supported; re-ingest to schema v2')
    payload = (directory / 'records.jsonl').read_bytes()
    if sha256(payload) != manifest['records_sha256']:
        raise ValueError('Corpus checksum mismatch; rebuild explicitly')
    records = [json.loads(line) for line in payload.splitlines() if line]
    if len(records) != manifest['records'] or len({r['id'] for r in records}) != len(records):
        raise ValueError('Invalid record count or duplicate IDs')
    for r in records:
        if r['content_sha256'] != sha256(r['text'].encode('utf-8')):
            raise ValueError('Record content checksum mismatch: ' + r['id'])
        validate_record(r)
    return records, manifest


RELEASE_STATUSES = {'pending', 'reference', 'withdrawn'}
VALIDATION_STATUSES = {
    'unvalidated', 'human_reviewed', 'compile_only', 'csim_passed', 'synthesis_passed'
}
ROLES = {'doc', 'design', 'testbench', 'build_script', 'config', 'other'}
SUPPORTED_TOOL_VERSIONS = {'2025.2', '2026.1'}

# Required provenance keys per record kind. New kinds (official examples, library
# components, experiment cases) are added alongside their ingesters; the contract turns
# missing provenance into a load-time error instead of a silently blank rendered source.
SOURCE_CONTRACT = {
    'document_section': {
        'required': ['document', 'version', 'language', 'file', 'file_sha256',
                     'page_start', 'page_end', 'section_path', 'parent_id', 'license'],
    },
    # A fix card keeps the same immutable source provenance as a PDF section,
    # while its structured applicability/action fields live on the record.
    'fix_card': {
        'required': ['document', 'version', 'language', 'file', 'file_sha256',
                     'page_start', 'page_end', 'section_path', 'parent_id', 'license',
                     'citation'],
    },
}

FIX_CARD_FIELDS = {
    'error_family', 'signature_terms', 'required_constructs', 'exclusions',
    'action', 'applicability', 'verification', 'source_citation',
}


def validate_source(kind, source):
    if not isinstance(source, dict):
        raise ValueError('Record source must be an object')
    contract = SOURCE_CONTRACT.get(kind)
    if contract is None:
        raise ValueError('Unknown record kind: ' + kind)
    missing = [key for key in contract['required'] if key not in source or source[key] is None]
    if missing:
        raise ValueError('Record source for kind %r missing provenance: %s' % (kind, missing))


def validate_record(record):
    if not isinstance(record, dict):
        raise ValueError('Record must be an object')
    required = {'id', 'title', 'text', 'kind', 'role', 'source', 'content_sha256',
                'tool', 'release', 'validation', 'quality', 'aliases'}
    missing = required - set(record)
    if missing:
        raise ValueError('Record missing fields: ' + ', '.join(sorted(missing)))
    if record['kind'] not in SOURCE_CONTRACT:
        raise ValueError('Unknown record kind: ' + record['kind'])
    if record['role'] not in ROLES:
        raise ValueError('Unknown record role: ' + record['role'])
    validate_source(record['kind'], record['source'])
    if record['kind'] == 'fix_card':
        missing_card_fields = FIX_CARD_FIELDS - set(record)
        if missing_card_fields:
            raise ValueError('Fix card missing fields: ' + ', '.join(sorted(missing_card_fields)))
        for field in ('signature_terms', 'required_constructs', 'exclusions'):
            if not isinstance(record[field], list) or not all(isinstance(v, str) for v in record[field]):
                raise ValueError('Fix card field must be a list of strings: ' + field)
        for field in ('error_family', 'action', 'applicability', 'verification', 'source_citation'):
            if not isinstance(record[field], str) or not record[field].strip():
                raise ValueError('Fix card field must be a nonempty string: ' + field)
    if (not isinstance(record['tool'], dict)
            or record['tool'].get('target_version') not in SUPPORTED_TOOL_VERSIONS):
        raise ValueError('Record tool target_version must be a supported Vitis version')
    if not isinstance(record['release'], dict) or record['release'].get('status') not in RELEASE_STATUSES:
        raise ValueError('Invalid record release status')
    if not isinstance(record['validation'], dict) or record['validation'].get('status') not in VALIDATION_STATUSES:
        raise ValueError('Invalid record validation status')


def record(identifier, title, text, source, *, kind, role='doc', quality=None, aliases=(),
           tool=None, release=None, validation=None, extra=None):
    tool = dict(tool or {})
    tool.setdefault('target_version', '2025.2')
    tool.setdefault('upstream_version', None)
    tool.setdefault('measured_version', None)
    release = dict(release or {})
    release.setdefault('status', 'pending')
    release.setdefault('reviewed_by', None)
    release.setdefault('reviewed_at', None)
    release.setdefault('note', None)
    validation = dict(validation or {})
    validation.setdefault('status', 'unvalidated')
    validation.setdefault('config_sha256', None)
    validation.setdefault('evidence', None)
    result = dict(id=identifier, title=title, text=text, kind=kind, role=role,
                  aliases=list(aliases), source=source,
                  content_sha256=sha256(text.encode('utf-8')),
                  tool=tool, release=release, validation=validation,
                  quality=quality or [])
    result.update(extra or {})
    validate_record(result)
    return result
