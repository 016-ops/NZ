"""Verify a fixed upstream checkout without starting it or installing dependencies."""
import argparse
import hashlib
import json
import subprocess
from pathlib import Path


PIN='4bbabab50ac6bfdbcd1f8e88bc6ea804e5978495'
FILES=['LICENSE','requirements.txt','START_ASSAULT_FIRE.ps1','server/account_db.py',
       'server/assaultfire_ap_auth.py','server/assaultfire_ds_spawner.py','server/assaultfire_server_v143b.py',
       'docs/STATUS.md','docs/ARCHITECTURE.md']


def verify(root):
    actual=subprocess.run(['git','-C',str(root),'rev-parse','HEAD'],capture_output=True,text=True,check=True).stdout.strip()
    if actual!=PIN:raise ValueError('Upstream SHA does not match the reviewed baseline')
    files=[]
    for name in FILES:
        local=(root/name).read_bytes()
        original=subprocess.run(['git','-C',str(root),'show',f'{PIN}:{name}'],capture_output=True,check=True).stdout
        # Compare git blob bytes after ignoring checkout line ending conversion only.
        unchanged=local.replace(b'\r\n',b'\n')==original.replace(b'\r\n',b'\n')
        if not unchanged:raise ValueError(f'Reviewed source changed: {name}')
        files.append({'path':name,'git_blob_sha256':hashlib.sha256(original).hexdigest(),'unchanged':unchanged})
    return {'url':'https://github.com/armangido/af-emulator','commit':actual,'license':'MIT',
            'reviewed_files':files,'retail_support':'PH 1.0.0.24 only','cn_current_compatibility':'UNVERIFIED',
            'automatic_launcher':'NOT_EXECUTED','dependency_install':'NOT_EXECUTED'}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,default=Path('upstream/af-emulator'))
    parser.add_argument('--output',type=Path,default=Path('evidence/upstream-baseline.json'))
    args=parser.parse_args();result=verify(args.root)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'commit':result['commit'],'reviewed_files':len(result['reviewed_files']),'unchanged':True}))


if __name__=='__main__':main()
