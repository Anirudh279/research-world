#!/usr/bin/env python3
"""Record installed renderer packages, runtime identities and bundled browser files."""
import argparse
import json
from pathlib import Path
from image_dependency_inventory import inventory

PROBE = r'''
import hashlib,json,pathlib,subprocess
root=pathlib.Path('/opt/render/node_modules')
def file_info(p):
 h=hashlib.sha256();size=0
 with p.open('rb') as f:
  while chunk:=f.read(1048576):
   size+=len(chunk)
   if size>536870912: raise ValueError('file limit')
   h.update(chunk)
 return dict(path=str(p),bytes=size,sha256=h.hexdigest())
packages=[]
for name in ('playwright','playwright-core'):
 p=root/name;m=json.loads((p/'package.json').read_text())
 packages.append(dict(name=m['name'],version=m['version'],license=m.get('license'),metadata=file_info(p/'package.json'),notices=[file_info(p/n) for n in ('LICENSE','NOTICE') if (p/n).is_file()]))
node=json.loads(subprocess.check_output(['node','-e','console.log(JSON.stringify({executable:process.execPath,versions:process.versions}))'],text=True,timeout=10))
node['binary']=file_info(pathlib.Path(node['executable']))
node['notice_candidates']=[file_info(p) for p in pathlib.Path('/usr/share/doc/nodejs').glob('*') if p.is_file() and any(x in p.name.lower() for x in ('copyright','license','notice'))]
browser_files=[];notice_candidates=[];symlinks=[]
for p in sorted(pathlib.Path('/ms-playwright').rglob('*')):
 if p.is_symlink():
  symlinks.append(dict(path=str(p),target=str(p.readlink())));continue
 if p.is_file():
  if len(browser_files)>20000:raise ValueError('file count limit')
  info=file_info(p);browser_files.append(info)
  if any(x in p.name.lower() for x in ('license','notice','credit','copying')):notice_candidates.append(info)
print(json.dumps(dict(packages=packages,node=node,playwright_browser_metadata=json.loads((root/'playwright-core/browsers.json').read_text()),browser_files=browser_files,browser_symlinks=symlinks,browser_notice_candidates=notice_candidates)))
'''
if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('image');parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    report=inventory(args.image,PROBE)
    report['schema']='renderer-runtime-inventory-v1'
    report['limitations']=['Browser metadata versions are package-declared; file hashes bind actual image contents.',
                          'Notice candidates use filenames only; absence does not establish absence of embedded notices.',
                          'Not a transitive component SBOM, license certification or vulnerability assessment.',
                          'Bundled browser symlinks are recorded, not followed for hashing.']
    args.output.write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({'packages':len(report['packages']),'browser_files':len(report['browser_files']),
                      'browser_notice_candidates':len(report['browser_notice_candidates']),
                      'node_notice_candidates':len(report['node']['notice_candidates'])}))
