#!/usr/bin/env python3
"""Inspect a local immutable image without network, mounts, or credentials.
Lists installed dpkg packages and hashes the copyright notices shipped in-image.
This is an inventory, not a license interpretation or vulnerability assessment.
"""
import argparse
import datetime
import hashlib
import json
from pathlib import Path
import re
import subprocess
import uuid

PROBE = r'''
import hashlib,json,pathlib,subprocess
rows=[]
output=subprocess.check_output(['dpkg-query','-W','-f=${binary:Package}\t${Version}\t${Architecture}\t${db:Status-Status}\n'],timeout=30,text=True)
for line in output.splitlines():
 name,version,architecture,status=line.split('\t')
 if status!='installed': continue
 base=name.split(':')[0]
 p=pathlib.Path('/usr/share/doc')/base/'copyright'
 row=dict(name=name,version=version,architecture=architecture,notice_path=str(p))
 try:
  resolved=p.resolve(strict=True)
  if not resolved.is_relative_to('/usr/share/doc'): raise ValueError('outside documentation tree')
  with resolved.open('rb') as f: data=f.read(2097153)
  if len(data)>2097152: raise ValueError('notice size limit')
  row.update(notice_status='present',notice_bytes=len(data),notice_sha256=hashlib.sha256(data).hexdigest())
 except (OSError,ValueError): row['notice_status']='missing_or_rejected'
 rows.append(row)
print(json.dumps(dict(packages=rows,os_release=pathlib.Path('/etc/os-release').read_text())))
'''

def inventory(image, probe=PROBE):
    if not re.fullmatch(r'sha256:[0-9a-f]{64}', image):
        raise ValueError('Use a full local immutable image ID')
    metadata=json.loads(subprocess.check_output(['docker','image','inspect',image],timeout=30))[0]
    if metadata['Id']!=image: raise ValueError('Image identity mismatch')
    name='wrs-inventory-'+uuid.uuid4().hex
    command=['docker','run','--name',name,'--rm','--pull=never','--network=none','--read-only',
             '--cap-drop=ALL','--security-opt=no-new-privileges','--memory=512m',
             '--pids-limit=32','--user=10001:10001','--entrypoint=python3',image,'-c',probe]
    try:
        result=subprocess.run(command,capture_output=True,timeout=60,check=True)
    finally:
        subprocess.run(['docker','rm','-f',name],capture_output=True,timeout=30)
    report=json.loads(result.stdout)
    report.update(schema='image-os-dependencies-v1',image_id=image,
                  observed_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                  architecture=metadata['Architecture'],os=metadata['Os'],
                  probe_sha256=hashlib.sha256(probe.encode()).hexdigest(),
                  limitations=['Installed dpkg metadata only; excludes Python, Node, manually installed binaries and bundled browsers.',
                               'Notice presence/hash does not establish license compliance or package provenance.',
                               'No network access, host mounts, or user credentials supplied.'])
    return report

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('image');parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    report=inventory(args.image)
    args.output.write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({'image_id':args.image,'packages':len(report['packages']),
                      'missing_notices':sum(p['notice_status']!='present' for p in report['packages'])}))
