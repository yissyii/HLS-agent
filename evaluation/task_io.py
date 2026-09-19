"""Explicit task inputs; no dataset discovery or hidden-answer lookup."""
import json
from pathlib import Path, PurePosixPath
import re

from agent.core.contracts import Material, TaskSpec
from serve.inference import Failure


def relative_name(value, allow_dot=False):
    if not isinstance(value, str) or not value or '\\' in value or any(ord(c) < 32 for c in value):
        raise Failure('input_error', 'Invalid manifest path')
    path = PurePosixPath(value)
    if (path.is_absolute() or '..' in path.parts or '.secrets' in path.parts or ':' in value
            or '"' in value or (str(path) == '.' and not allow_dot)):
        raise Failure('input_error', 'Expected safe task-relative file path')
    return str(path)


def load_manifest(path):
    path = Path(path).resolve()
    manifest = json.loads(path.read_text(encoding='utf-8'))
    for name, pattern in [('id', r'[A-Za-z0-9_][A-Za-z0-9_-]*'),
                          ('top_function', r'[A-Za-z_][A-Za-z0-9_]*')]:
        if not isinstance(manifest.get(name), str) or not re.fullmatch(pattern, manifest[name]):
            raise Failure('input_error', name + ' must be a simple identifier')
    manifest['problem_file'] = relative_name(manifest['problem_file'])
    manifest['source_file'] = relative_name(manifest.get('source_file', 'kernel.cpp'))
    if Path(manifest['source_file']).suffix not in {'.c', '.cpp', '.cc', '.cxx'}:
        raise Failure('input_error', 'Generated source must be C/C++')
    for name in ('testbench_files', 'design_files', 'support_files', 'include_dirs', 'model_visible'):
        values = manifest.get(name, ['.'] if name == 'include_dirs' else [])
        if not isinstance(values, list):
            raise Failure('input_error', name + ' must be a list')
        manifest[name] = [relative_name(v, allow_dot=name == 'include_dirs') for v in values]
    manifest['cxx_standard'] = manifest.get('cxx_standard', 'c++14')
    if manifest['cxx_standard'] not in {'c++11', 'c++14', 'c++17'}:
        raise Failure('input_error', 'Unsupported C++ standard')
    manifest['feedback_policy'] = manifest.get('feedback_policy', 'category_only')
    if manifest['feedback_policy'] not in {'category_only', 'compiler_diagnostics', 'functional_diagnostics', 'public_diagnostics'}:
        raise Failure('input_error', 'feedback_policy must be category_only, compiler_diagnostics, functional_diagnostics or public_diagnostics')
    names = [manifest['problem_file']] + sum((manifest[k] for k in ('testbench_files', 'design_files', 'support_files')), [])
    all_names = names + [manifest['source_file']]
    if len({n.casefold() for n in all_names}) != len(all_names):
        raise Failure('input_error', 'Manifest paths must not collide (including case aliases)')
    for a in all_names:
        if any(b.casefold().startswith(a.casefold() + '/') for b in all_names if b != a):
            raise Failure('input_error', 'File/directory collision in manifest')
    if not set(manifest['model_visible']).issubset(set(names) - {manifest['problem_file']}):
        raise Failure('input_error', 'model_visible must name declared dependencies')
    for name in names:
        target = (path.parent / name).resolve()
        if not target.is_relative_to(path.parent) or not target.is_file():
            raise Failure('input_error', 'Dependency missing or outside task: ' + name)
    return manifest, names


def load_task(problem_path, manifest_path=None):
    problem = Path(problem_path).read_bytes()
    if not problem.decode('utf-8').strip():
        raise Failure('input_error', 'Empty problem')
    if manifest_path is None:
        return TaskSpec(problem)
    path = Path(manifest_path).resolve()
    manifest, names = load_manifest(path)
    if (path.parent / manifest['problem_file']).read_bytes() != problem:
        raise Failure('input_error', 'Manifest problem differs from the supplied problem bytes')
    materials = [Material(name, (path.parent / name).read_bytes(), name in manifest['model_visible'])
                 for name in names if name != manifest['problem_file']]
    directories = {'.'}
    for material in materials:
        directories.update(str(p) for p in PurePosixPath(material.name).parents)
    if not set(manifest['include_dirs']).issubset(directories):
        raise Failure('input_error', 'Include directory has no declared input files')
    return TaskSpec(problem, manifest, materials)
