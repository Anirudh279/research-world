"""Check build boundaries without extracting or executing package contents."""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import tarfile
import zipfile

FORBIDDEN = {'evaluation', 'evidence', 'data', '.env', '.git', '.venv', '__pycache__'}


def check(path):
    path = Path(path)
    if path.name.endswith('.whl'):
        with zipfile.ZipFile(path) as package:
            members = [(i.filename, bool((i.external_attr >> 16) & 0o170000 == 0o120000)) for i in package.infolist()]
        normalized = [name for name, _ in members]
        required = 'web_research_sim/static/index.html'
    elif path.name.endswith('.tar.gz'):
        with tarfile.open(path, 'r:gz') as package:
            members = [(i.name, i.issym() or i.islnk()) for i in package.getmembers()]
        roots = {PurePosixPath(name).parts[0] for name, _ in members}
        if len(roots) != 1:
            raise ValueError('Source archive must have one root')
        normalized = ['/'.join(PurePosixPath(name).parts[1:]) for name, _ in members]
        required = 'src/web_research_sim/static/index.html'
    else:
        raise ValueError('Expected wheel or source tar.gz')
    for (name, linked), relative in zip(members, normalized):
        p = PurePosixPath(name)
        if linked or p.is_absolute() or '..' in p.parts or '\\' in name:
            raise ValueError('Unsafe archive member')
        if FORBIDDEN.intersection(PurePosixPath(relative).parts):
            raise ValueError('Private or evaluator content in distribution')
    if required not in normalized:
        raise ValueError('Dashboard missing from distribution')
    return {'artifact':path.name,'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
            'members':len(members),'package_boundary':'passed',
            'limitations':'Path boundary check only; not a secret scan or full release certification.'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('artifacts', nargs='+')
    args = parser.parse_args()
    print(json.dumps([check(p) for p in args.artifacts], indent=2))
