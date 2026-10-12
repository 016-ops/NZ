"""Index candidate metadata symbols only; do not infer wire IDs, offsets, or widths."""
import argparse
import hashlib
import json
import re
from pathlib import Path


def inspect(path):
    path=Path(path)
    if path.suffix.lower()!='.tdr' or path.stat().st_size>64*1024*1024:
        raise ValueError('Input must be a .tdr file no larger than 64 MiB')
    data=path.read_bytes()
    symbols=[]
    # Only null-terminated ASCII identifiers are retained. Other strings may contain credentials.
    for match in re.finditer(rb'(?<![A-Za-z0-9_])[A-Za-z_][A-Za-z0-9_]{3,127}\x00',data):
        name=match.group(0)[:-1].decode('ascii')
        if name.startswith(('C2ZN','ZN2C','CS2C','C2CS','C2GEO','GEO2C','DS2','Req','Res','Ntf')):
            symbols.append({'name':name,'file_offset':match.start(),'meaning':'UNVERIFIED_SYMBOL_ONLY'})
    return {'file':path.name,'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest(),
            'candidate_symbols':symbols,'wire_schema':'UNKNOWN','message_ids':'UNKNOWN',
            'evidence':'FILE_INSPECTION_NOT_PROTOCOL_VALIDATION'}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root',type=Path)
    parser.add_argument('output',type=Path)
    args=parser.parse_args()
    root=args.root.resolve(strict=True);output=args.output.resolve()
    if output==root or root in output.parents:raise ValueError('Output must be outside source')
    result=[inspect(path)for path in sorted(root.glob('*.tdr'))]
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'files':len(result),'symbols':sum(len(x['candidate_symbols'])for x in result),'status':'UNVERIFIED_SYMBOL_ONLY'}))


if __name__=='__main__':main()
