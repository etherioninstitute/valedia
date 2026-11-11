#!/usr/bin/env python3
"""Static, local repository navigation with content-addressed freshness. No repo imports."""
import sys
sys.dont_write_bytecode = True
import argparse
import ast
import contextlib
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import tempfile
import time
import uuid

VERSION = 'VALEDIA-REPO-MAP-1.0'
REPOMIX_VERSION = '1.18.1'
INTEGRITY = 'sha512-XtV1qD6akJSigQ3fBRex5v+rWcGa4i9cm2FL/M/PEtzO3u1uDiso3CtNbhlZiMIqJstAdy8mj2m1ynOzNnNB3g=='
DEFAULT_ROOT = Path(__file__).resolve().parents[1]
MAX_FILE = 4 * 1024 * 1024
MAX_TOTAL = 64 * 1024 * 1024
DENY_DIRS = {'.git', '.repo-map', '__pycache__', 'node_modules', 'state', 'models', 'model', 'history', 'histories', 'outputs', 'output', 'cache', '.cache', 'keys', 'secrets', 'credentials', '.aws', '.ssh', 'venv', '.venv'}
EXACT = {'AGENTS.md', 'repomix.config.json', '.repomixignore'}

class MapError(Exception):
    pass

def encoded(value):
    return (json.dumps(value, sort_keys=True, indent=2, ensure_ascii=True) + '\n').encode()

def digest(data):
    return hashlib.sha256(data).hexdigest()

def regular_read(path):
    """No symlink following, including during open; callers walk parent dirs likewise."""
    fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))
    with os.fdopen(fd, 'rb') as f:
        st = os.fstat(f.fileno())
        if not stat.S_ISREG(st.st_mode) or st.st_size > MAX_FILE:
            raise MapError('non-regular/oversized input: ' + str(path))
        data = f.read(MAX_FILE + 1)
        if len(data) > MAX_FILE:
            raise MapError('oversized input')
        return data

def safe_name(path):
    return not (any(c in str(path) for c in '\n\r\t') or path.name.startswith('.env') or re.search(r'(^|[._-])(secret|credentials?|private[_-]?key|api[_-]?key)([._-]|$)', path.name, re.I))

def collect(root):
    """Explicit extension/location allowlist; never opens state, symlinks or sockets."""
    blobs, skipped = {}, []
    for base in ['src', 'docs', 'tools']:
        start = root/base
        if start.is_symlink():
            raise MapError('symlink input root: ' + base)
        if not start.exists():
            continue
        for parent, dirs, files in os.walk(start, followlinks=False):
            parent = Path(parent)
            dirs[:] = sorted(d for d in dirs if d not in DENY_DIRS and not (parent/d).is_symlink() and safe_name(Path(d)))
            for name in sorted(files):
                p = parent/name
                rel = p.relative_to(root).as_posix()
                allowed = (base == 'src' and p.suffix == '.py') or (base == 'docs' and p.suffix.lower() in {'.md', '.txt', '.json'}) or (base == 'tools' and p.suffix in {'.py', '.sh', '.json', '.md'})
                if not allowed:
                    continue
                if p.is_symlink() or not stat.S_ISREG(p.lstat().st_mode) or not safe_name(p):
                    skipped.append(rel)
                    continue
                blobs[rel] = regular_read(p)
    for name in sorted(EXACT):
        p = root/name
        if p.is_symlink():
            raise MapError('symlink config: ' + name)
        if p.exists():
            blobs[name] = regular_read(p)
    if sum(map(len, blobs.values())) > MAX_TOTAL:
        raise MapError('allowlist exceeds 64 MiB; review resource bounds')
    return dict(sorted(blobs.items())), sorted(skipped)

def runtime_paths(root, runtime_arg=None, tmp_arg=None):
    runtime = Path(runtime_arg or os.environ.get('REPOMIX_RUNTIME', root.parent/'runtime'/'repomix-1.18.1')).expanduser().resolve()
    tmp = Path(tmp_arg or os.environ.get('REPO_MAP_TMPDIR', root.parent/'tmp'/'repomix')).expanduser().resolve()
    node = shutil.which(os.environ.get('REPO_MAP_NODE', 'node'))
    if not node:
        raise MapError('Node >=22 required; set REPO_MAP_NODE')
    return runtime, tmp, Path(node).resolve()

def tool_snapshot(runtime, node):
    lock = json.loads(regular_read(runtime/'package-lock.json'))
    entry = lock['packages']['node_modules/repomix']
    package = runtime/'node_modules'/'repomix'
    meta = json.loads(regular_read(package/'package.json'))
    if entry.get('version') != REPOMIX_VERSION or entry.get('integrity') != INTEGRITY or meta.get('version') != REPOMIX_VERSION:
        raise MapError('Repomix pin/integrity mismatch')
    result = subprocess.run([str(node), '--version'], capture_output=True, timeout=10, check=True)
    version = result.stdout.decode().strip()
    if int(version.lstrip('v').split('.')[0]) < 22:
        raise MapError('Node >=22 required')
    hashes = {'package-lock.json': digest(regular_read(runtime/'package-lock.json')), 'package.json': digest(regular_read(runtime/'package.json'))}
    for p in sorted(package.rglob('*')):
        if p.is_symlink():
            raise MapError('symlink in Repomix package')
        if p.is_file():
            hashes['repomix/' + p.relative_to(package).as_posix()] = digest(regular_read(p))
    return {'repomix': REPOMIX_VERSION, 'npm_integrity': INTEGRITY, 'node': version, 'files': hashes}

def inputs(root, runtime, node):
    blobs, skipped = collect(root)
    tool = tool_snapshot(runtime, node)
    state = {'format': VERSION, 'python': sys.version.split()[0], 'files': {p: digest(b) for p,b in blobs.items()}, 'excluded_special_or_sensitive': skipped, 'tool': tool}
    return blobs, state, digest(encoded(state))

def module_id(rel):
    parts = Path(rel).relative_to('src').with_suffix('').parts
    return '.'.join(parts[:-1] if parts[-1] == '__init__' else parts)

def module_index(blobs):
    mods = {}
    for path, content in blobs.items():
        if not path.startswith('src/') or not path.endswith('.py'):
            continue
        mid = module_id(path)
        if mid in mods:
            raise MapError('ambiguous module identifier: ' + mid)
        mods[mid] = {'module': mid, 'path': path, 'sha256': digest(content), 'package': path.endswith('/__init__.py'), 'lines': len(content.splitlines()), 'symbols': [], 'imports': [], 'dynamic_imports': [], 'syntax_error': None}
    stdlib = getattr(sys, 'stdlib_module_names', set())
    for mid, item in mods.items():
        try:
            tree = ast.parse(blobs[item['path']], filename=item['path'])
        except (SyntaxError, ValueError, UnicodeError) as e:
            item['syntax_error'] = {'type': type(e).__name__, 'line': getattr(e, 'lineno', None), 'message': getattr(e, 'msg', 'invalid source encoding')}
            continue
        def symbols(node, prefix=''):
            for child in ast.iter_child_nodes(node):
                if isinstance(child, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                    name = prefix + child.name
                    item['symbols'].append({'name': name, 'kind': 'class' if isinstance(child, ast.ClassDef) else 'async_function' if isinstance(child, ast.AsyncFunctionDef) else 'function', 'line': child.lineno, 'end_line': child.end_lineno})
                    symbols(child, name + '.')
                else:
                    symbols(child, prefix)
        symbols(tree)
        package = mid if item['package'] else mid.rpartition('.')[0]
        def classify(target, relative=False):
            if target in mods:
                return 'internal'
            if any(m.startswith(target + '.') for m in mods) and target:
                return 'internal_namespace'
            if relative:
                return 'unresolved_relative'
            return 'stdlib' if target.split('.')[0] in stdlib else 'external_or_unresolved'
        for n in ast.walk(tree):
            if isinstance(n, ast.Import):
                for alias in n.names:
                    item['imports'].append({'line': n.lineno, 'target': alias.name, 'kind': classify(alias.name), 'form': 'import'})
            elif isinstance(n, ast.ImportFrom):
                target = n.module or ''
                invalid_relative = False
                if n.level:
                    parts = package.split('.') if package else []
                    if n.level > len(parts):
                        target = '.' * n.level + target
                        invalid_relative = True
                    else:
                        target = '.'.join(parts[:len(parts)-n.level+1] + ([target] if target else []))
                candidates = []
                for a in n.names:
                    candidate = target + '.' + a.name if target else a.name
                    if candidate in mods:
                        candidates.append(candidate)
                # Include base module, plus definite imported submodules; names may be symbols.
                for t in dict.fromkeys([target] + candidates):
                    item['imports'].append({'line': n.lineno, 'target': t, 'kind': 'unresolved_relative' if invalid_relative else classify(t, bool(n.level)), 'form': 'from', 'names': [a.name for a in n.names], 'level': n.level})
            elif isinstance(n, ast.Call):
                func = n.func
                name = func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else ''
                if name in {'__import__', 'import_module', 'exec', 'eval'}:
                    # Never serialize arguments: they could contain code or secrets.
                    item['dynamic_imports'].append({'line': n.lineno, 'mechanism': name, 'resolution': 'not_statically_resolved'})
        item['symbols'].sort(key=lambda x: (x['line'], x['name']))
        item['imports'].sort(key=lambda x: (x['line'], x['target']))
    return {'format': VERSION, 'scope': 'All allowlisted recursive src/**/*.py; entire AST, including lazy/conditional imports.', 'limitations': ['Static imports are possible dependencies, not proof an edge executes.', 'external_or_unresolved does not distinguish installed dependencies from missing local modules.', 'Dynamic loading, monkey-patching, alias calls, runtime paths and external services are not fully resolved.', 'No repository modules imported/executed; no behavioral, security or profitability audit.'], 'modules': list(mods.values())}

def navigation(index, fingerprint):
    mods = index['modules']
    edges = sum(len(m['imports']) for m in mods)
    syntax = [m for m in mods if m['syntax_error']]
    out = ['# Valedia repository navigation', '', 'Source-derived static facts. Curated roles are in docs/repository-map/ARCHITECTURE.md.', '', f'Input fingerprint: `{fingerprint}`', f'Modules: {len(mods)}; import statements/targets: {edges}; syntax failures: {len(syntax)}.', '', 'Run `python3 tools/repo_map.py ensure` before using this generation. This is not a runtime/call-graph audit.', '', '## MiroFish', '']
    for m in mods:
        if 'mirofish' in m['module'].lower():
            out.append(f"- `{m['path']}`: {len(m['symbols'])} symbols; {len(m['imports'])} static import targets")
    out += ['', '## All modules', '', '| Path | Lines | Symbols | Internal targets | Unresolved/external | Dynamic hints |', '|---|---:|---:|---:|---:|---:|']
    for m in mods:
        kinds = [i['kind'] for i in m['imports']]
        out.append(f"| `{m['path']}` | {m['lines']} | {len(m['symbols'])} | {sum(k.startswith('internal') for k in kinds)} | {sum(k in {'external_or_unresolved','unresolved_relative'} for k in kinds)} | {len(m['dynamic_imports'])} |")
    if syntax:
        out += ['', '## Syntax failures', ''] + [f"- `{m['path']}`: line {m['syntax_error']['line']}, {m['syntax_error']['type']}" for m in syntax]
    out += ['', 'For symbol names/line numbers and import targets query `modules.json`. Modules named test_* are indexed; their tests were not executed.', '']
    return '\n'.join(out).encode()

def validate_config(blobs):
    # A JSON allowlist, not arbitrary Repomix options: avoid executable/instruction/remote config.
    try:
        value = json.loads(blobs['repomix.config.json'])
    except (KeyError, ValueError) as e:
        raise MapError('valid repomix.config.json required') from e
    expected = standard_config()
    if value != expected:
        raise MapError('Repomix config differs from safe schema; update standard_config and review together')
    return value

def standard_config():
    return {'input': {'maxFileSize': MAX_FILE}, 'output': {'style': 'xml', 'filePath': 'repomix.xml', 'files': False, 'fileSummary': True, 'directoryStructure': True, 'includeFullDirectoryStructure': False, 'copyToClipboard': False, 'git': {'sortByChanges': False, 'includeDiffs': False, 'includeLogs': False}}, 'include': ['src/**/*.py', 'docs/**/*.md', 'docs/**/*.txt', 'docs/**/*.json', 'tools/**/*.py', 'tools/**/*.sh', 'tools/**/*.json', 'tools/**/*.md', 'AGENTS.md'], 'ignore': {'useGitignore': False, 'useDotIgnore': False, 'useDefaultPatterns': True, 'customPatterns': []}, 'security': {'enableSecurityCheck': True}}

def run_repomix(root, runtime, node, tmp, blobs, full=False):
    config = validate_config(blobs)
    tmp.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='repomap-', dir=tmp) as name:
        work = Path(name)
        stage = work/'input'
        stage.mkdir()
        config_dir = work/'isolated-config'
        config_dir.mkdir()
        for rel, data in blobs.items():
            if rel in {'repomix.config.json', '.repomixignore'}:
                continue
            p = stage/rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(data)
        output = work/'repomix.xml'
        config['output']['filePath'] = str(output)
        config['output']['files'] = full
        config_path = work/'config.json'
        config_path.write_bytes(encoded(config))
        # Only a newly authored JSON config is visible; hostile repo/home JS config never staged.
        env = {'PATH': str(node.parent) + os.pathsep + '/usr/bin:/bin', 'XDG_CONFIG_HOME': str(config_dir), 'TMPDIR': str(work), 'TEMP': str(work), 'TMP': str(work), 'NO_COLOR': '1', 'DO_NOT_TRACK': '1', 'CI': 'true'}
        result = subprocess.run([str(node), str(runtime/'node_modules/repomix/bin/repomix.cjs'), str(stage), '--config', str(config_path), '--quiet'], cwd=work, env=env, capture_output=True, timeout=180)
        if result.returncode or not output.exists():
            # Never echo scanner output, which may contain sensitive source snippets.
            raise MapError(f'Repomix CLI failed (exit {result.returncode}); no generation published')
        packed = output.read_bytes()
        if len(packed) > (MAX_TOTAL*2 if full else 512*1024):
            raise MapError('Repomix output exceeds resource bound')
        return packed

def map_dir(root):
    p = root/'.repo-map'
    if p.is_symlink():
        raise MapError('symlink map directory')
    p.mkdir(exist_ok=True)
    gens = p/'generations'
    if gens.is_symlink():
        raise MapError('symlink generations directory')
    gens.mkdir(exist_ok=True)
    return p

@contextlib.contextmanager
def locked(root):
    base = map_dir(root)
    fd = os.open(base/'build.lock', os.O_CREAT | os.O_RDWR | getattr(os, 'O_NOFOLLOW', 0), 0o600)
    with os.fdopen(fd, 'r+') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield base

def current(root, state, fingerprint):
    base = root/'.repo-map'
    try:
        if base.is_symlink() or (base/'generations').is_symlink():
            return None
        pointer = json.loads(regular_read(base/'current.json'))
        name = pointer['generation']
        if not re.fullmatch(r'[0-9a-f]{64}-[0-9a-f]{12}', name):
            return None
        gen = base/'generations'/name
        if gen.is_symlink():
            return None
        raw = regular_read(gen/'manifest.json')
        if digest(raw) != pointer['manifest_sha256']:
            return None
        manifest = json.loads(raw)
        if manifest['input_fingerprint'] != fingerprint or manifest['inputs'] != state:
            return None
        if set(manifest['artifacts']) != {'modules.json', 'navigation.md', 'repomix.xml'}:
            return None
        for rel, sha in manifest['artifacts'].items():
            if digest(regular_read(gen/rel)) != sha:
                return None
        return gen
    except (OSError, ValueError, KeyError, TypeError, MapError):
        return None

def publish(base, fingerprint, state, artifacts):
    name = fingerprint + '-' + uuid.uuid4().hex[:12]
    gen = base/'generations'/name
    gen.mkdir()
    for rel, data in artifacts.items():
        (gen/rel).write_bytes(data)
    manifest = {'format': VERSION, 'input_fingerprint': fingerprint, 'inputs': state, 'artifacts': {p:digest(b) for p,b in artifacts.items()}, 'created_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())}
    manifest_bytes = encoded(manifest)
    (gen/'manifest.json').write_bytes(manifest_bytes)
    # Readers resolve pointer once and stay in immutable generation; old generations retained.
    pointer = base/('.current-' + uuid.uuid4().hex)
    with pointer.open('wb') as f:
        f.write(encoded({'generation': name, 'manifest_sha256': digest(manifest_bytes)}))
        f.flush()
        os.fsync(f.fileno())
    os.replace(pointer, base/'current.json')
    return gen

def ensure(root, runtime, tmp, node, force=False):
    with locked(root) as base:
        for attempt in range(3):
            blobs, state, fingerprint = inputs(root, runtime, node)
            gen = current(root, state, fingerprint)
            if gen and not force:
                return gen, False
            index = module_index(blobs)
            artifacts = {'modules.json': encoded(index), 'navigation.md': navigation(index,fingerprint), 'repomix.xml': run_repomix(root,runtime,node,tmp,blobs)}
            # Changes while AST/CLI work is running must never be called current.
            _, after, after_fingerprint = inputs(root,runtime,node)
            if after != state or after_fingerprint != fingerprint:
                continue
            return publish(base,fingerprint,state,artifacts), True
        raise MapError('inputs changed during three build attempts; rerun ensure')

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['ensure','check','refresh','watch','pack'])
    parser.add_argument('--root', type=Path, default=DEFAULT_ROOT)
    parser.add_argument('--runtime')
    parser.add_argument('--tmpdir')
    parser.add_argument('--interval', type=float, default=5)
    args = parser.parse_args(argv)
    root = args.root.expanduser().resolve()
    try:
        runtime,tmp,node = runtime_paths(root,args.runtime,args.tmpdir)
        if args.command == 'check':
            _,state,fingerprint = inputs(root,runtime,node)
            gen = current(root,state,fingerprint)
            if not gen:
                print('STALE: run python3 tools/repo_map.py ensure',file=sys.stderr)
                return 1
            print('CURRENT ' + str(gen.relative_to(root)))
        elif args.command == 'pack':
            gen,_ = ensure(root,runtime,tmp,node)
            blobs,state,fingerprint = inputs(root,runtime,node)
            if not current(root,state,fingerprint):
                raise MapError('inputs changed; rerun pack')
            data = run_repomix(root,runtime,node,tmp,blobs,full=True)
            _,after,_ = inputs(root,runtime,node)
            if after != state:
                raise MapError('inputs changed during pack; rerun')
            dest = root/'.repo-map'/('fullsource-' + fingerprint + '.xml')
            pending = dest.with_name('.pack-' + uuid.uuid4().hex)
            pending.write_bytes(data)
            os.replace(pending,dest)
            print('EXPLICIT FULL SOURCE PACK ' + str(dest.relative_to(root)))
        else:
            if args.command == 'watch' and args.interval < 1:
                raise MapError('watch interval must be >=1 second')
            while True:
                gen,changed = ensure(root,runtime,tmp,node,args.command=='refresh')
                if changed or args.command != 'watch':
                    print(('REFRESHED ' if changed else 'CURRENT ') + str(gen.relative_to(root)),flush=True)
                if args.command != 'watch':
                    break
                time.sleep(args.interval)
        return 0
    except KeyboardInterrupt:
        return 130
    except (MapError,OSError,ValueError,KeyError,subprocess.SubprocessError) as e:
        print('REPO MAP ERROR: ' + str(e),file=sys.stderr)
        return 2

if __name__ == '__main__':
    raise SystemExit(main())
