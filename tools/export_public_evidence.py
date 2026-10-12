"""Publish aggregate fingerprints and sanitized test reports; never copy client resources."""
import json
import os
from pathlib import Path
from tools.client_inventory import compare


def main():
    root=Path(__file__).resolve().parents[1]
    private=root/'evidence/private'
    before=json.loads((private/'client-manifest.summary.json').read_text(encoding='utf-8'))
    after=json.loads((private/'client-manifest-after.summary.json').read_text(encoding='utf-8'))
    differences=compare(private/'client-manifest.csv',private/'client-manifest-after.csv')
    if any(differences.values()):raise SystemExit('Client changed; inspect private diff before publishing')
    versions=json.loads((private/'version-evidence.json').read_text(encoding='utf-8-sig'))
    result={
        'record_date':'2026-10-13','profile':'cn-current','source':'USER_PROVIDED_LOCAL_DIRECTORY',
        'authenticity':'UNVERIFIED','files':before['files'],'bytes':before['bytes'],
        'extensions':before['extensions'],'manifest_before_sha256':before['manifest_sha256'],
        'manifest_after_sha256':after['manifest_sha256'],'differences':differences,
        'client_files_unchanged':True,'client_executed':False,'patches_applied':False,
        'entrypoint_evidence':versions,'private_indexes_published':False,
    }
    (root/'evidence/client-baseline.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    replacements={str(root):'<WORKSPACE>',os.environ.get('USERPROFILE',''):'<USERPROFILE>'}
    # Keep local originals under ignored private evidence; publish only redacted copies.
    for path in sorted((root/'evidence').rglob('*')):
        if not path.is_file() or private in path.parents or path.name=='client-baseline.json':continue
        if path.suffix not in ('.json','.txt'):continue
        original=path.read_text(encoding='utf-8')
        sanitized=original
        for value,replacement in replacements.items():
            if value:
                sanitized=sanitized.replace(value,replacement).replace(value.replace('\\','\\\\'),replacement)
        if original!=sanitized:
            backup=private/'raw-tests'/path.relative_to(root/'evidence')
            backup.parent.mkdir(parents=True,exist_ok=True)
            if not backup.exists():backup.write_text(original,encoding='utf-8')
            path.write_text(sanitized,encoding='utf-8')
    print(json.dumps({'files':before['files'],'bytes':before['bytes'],'unchanged':True,'test_reports':'sanitized'}))


if __name__=='__main__':main()
