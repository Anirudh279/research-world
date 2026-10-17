#!/usr/bin/env python3
"""Offline lock/installed metadata inventory; no package imports or license inference.
Run .venv/bin/python scripts/dependency_inventory.py from the repository root.
"""
from __future__ import annotations
import argparse
import hashlib
import importlib.metadata as md
import json
from pathlib import Path
import re
import tomllib

MAX_FILE = 1024 * 1024


def normalized(name):
    return re.sub(r'[-_.]+', '-', name).lower()


def read_license(path, venv):
    resolved = path.resolve()
    if not resolved.is_relative_to(venv.resolve()):
        return {'status': 'outside_venv_rejected'}, None
    if not resolved.is_file():
        return {'status': 'missing_or_not_file'}, None
    with resolved.open('rb') as stream:
        data = stream.read(MAX_FILE + 1)
    if len(data) > MAX_FILE:
        return {'status': 'size_limit'}, None
    try:
        text = data.decode('utf-8')
    except UnicodeDecodeError:
        return {'status': 'non_utf8', 'sha256': hashlib.sha256(data).hexdigest()}, None
    return {'status': 'copied', 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}, text


def inventory(root):
    root = Path(root).resolve()
    venv = root / '.venv'
    lock_bytes = (root / 'uv.lock').read_bytes()
    lock = tomllib.loads(lock_bytes.decode())
    installed = {}
    for site in sorted(venv.glob('lib/python*/site-packages')):
        for info in sorted(site.glob('*.dist-info')):
            if not info.resolve().is_relative_to(venv.resolve()):
                raise ValueError('Distribution metadata escaped the virtual environment')
            for metadata_file in ('METADATA', 'RECORD'):
                candidate = info / metadata_file
                if candidate.exists() and (not candidate.resolve().is_relative_to(venv.resolve()) or candidate.stat().st_size > MAX_FILE):
                    raise ValueError('Unsafe or oversized distribution metadata')
            dist = md.PathDistribution(info)
            installed.setdefault(normalized(dist.metadata['Name']), []).append((dist, info))
    notices = ['THIRD PARTY PYTHON NOTICES', '',
               'Generated offline from uv.lock and this workspace .venv metadata.',
               'License text is copied verbatim as UTF-8; metadata is not legal interpretation.',
               'Includes installed development packages. Conditional/uninstalled lock packages may lack notices.',
               'Excludes OS image packages, Node packages and Playwright browser binaries.',
               'This is not complete compliance certification or installed wheel provenance verification.', '']
    packages = []
    for pkg in sorted(lock['package'], key=lambda p: (p['name'], p['version'])):
        name = normalized(pkg['name'])
        row = {'name': pkg['name'], 'version': pkg['version'], 'source': pkg['source'],
               'wheels': [{'url': w.get('url'), 'hash': w.get('hash')} for w in pkg.get('wheels', [])],
               'sdist': pkg.get('sdist'), 'resolution_markers': pkg.get('resolution-markers', []),
               'dependencies': pkg.get('dependencies', []),
               'scope': 'first_party' if 'editable' in pkg['source'] else 'runtime_or_dev_or_conditional_unknown',
               'installed_versions': [d.version for d, _ in installed.get(name, [])],
               'installed_match': any(d.version == pkg['version'] for d, _ in installed.get(name, [])),
               'license_metadata': [], 'license_files': []}
        for dist, info in installed.get(name, []):
            if dist.version != pkg['version']:
                continue
            meta = dist.metadata
            row['license_metadata'].append({'expression': meta.get('License-Expression'),
                'legacy_license': meta.get('License'),
                'classifiers': [c for c in meta.get_all('Classifier', []) if c.startswith('License ::')],
                'classification': 'declared_metadata_only_no_inference'})
            candidates = set()
            for advertised in meta.get_all('License-File', []):
                candidates.add(info / advertised)
                candidates.add(info / 'licenses' / advertised)
            for file in dist.files or []:
                if re.match(r'(?i)^(licen[cs]e|copying|notice|copyright)([._-].*)?$', Path(str(file)).name):
                    candidates.add(Path(dist.locate_file(file)))
            seen = set()
            for path in sorted(candidates, key=str):
                resolved = path.resolve()
                if resolved in seen:
                    continue
                seen.add(resolved)
                result, text = read_license(path, venv)
                # Only publish project-relative paths. Do not expose external link targets.
                result['path'] = str(path.relative_to(venv)) if path.is_relative_to(venv) else '[outside venv]'
                row['license_files'].append(result)
                if text is not None and row['scope'] != 'first_party':
                    notices.extend([f"=== {pkg['name']} {pkg['version']} : {result['path']} ===", text, ''])
        row['license_status'] = 'metadata_available' if row['license_metadata'] else 'unknown_uninstalled_or_version_mismatch'
        packages.append(row)
    known = {normalized(p['name']) for p in lock['package']}
    output = {'schema': 'python-dependency-inventory-v1', 'lock_sha256': hashlib.sha256(lock_bytes).hexdigest(),
              'lock_requires_python': lock.get('requires-python'),
              'lock_resolution_markers': lock.get('resolution-markers', []),
              'scope_policy': 'All locked packages, including conditional and development dependencies. Runtime/dev reachability is not inferred; per-package scope explicitly unknown except editable first-party.',
              'verification_limits': ['Installed match compares declared name/version only, not wheel hash or installed file integrity.',
                  'No network, package execution, license inference or legal approval.',
                  'Uninstalled packages have no locally verified license metadata or text.',
                  'OS image, Node packages and Playwright browser binaries are excluded.'],
              'installed_not_locked': sorted(name for name in installed if name not in known), 'packages': packages}
    return output, '\n'.join(notices) + '\n'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    data, notices = inventory(args.root)
    (args.root / 'docs/dependency-inventory.json').write_text(json.dumps(data, indent=2, sort_keys=True) + '\n')
    (args.root / 'THIRD_PARTY_NOTICES.txt').write_text(notices)
    print(f"Inventoried {len(data['packages'])} locked Python packages; scope limitations recorded.")


if __name__ == '__main__':
    main()
