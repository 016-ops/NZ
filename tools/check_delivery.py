"""Inspect staged paths and content before publishing; never stage client/runtime data."""
import re
import subprocess
from pathlib import Path


def main():
    root=Path(__file__).resolve().parents[1]
    names=subprocess.run(['git','-C',str(root),'diff','--cached','--name-only','-z'],capture_output=True,check=True).stdout.decode('utf-8').split('\0')
    denied={'.exe','.dll','.sys','.sys2','.upk','.udk','.umap','.u','.tdr','.dat','.db','.sqlite3','.pem','.key','.pyc','.pdb'}
    private={'nz','runtime','upstream','private','__pycache__','.venv'}
    failures=[];count=0
    for name in filter(None,names):
        path=Path(name)
        if path.suffix.lower()in denied or private.intersection(path.parts):failures.append(name+': excluded data');continue
        absolute=root/path
        if not absolute.exists():continue
        data=absolute.read_bytes();count+=1
        if len(data)>10*1024*1024:failures.append(name+': unexpectedly large')
        if re.search(rb'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|gh[pousr]_[A-Za-z0-9]{30,}',data):failures.append(name+': secret pattern')
    if failures:raise SystemExit('\n'.join(failures))
    print(f'Checked {count} staged files: no client/runtime binaries or known private-key/token patterns')


if __name__=='__main__':main()
