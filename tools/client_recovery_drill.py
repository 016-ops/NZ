"""仅在已登记工作副本的版本文本上演练恢复；原客户端始终只读。"""
import argparse
import json
from pathlib import Path
from tools.client_workspace import checked_path, copy_verified, digest


def main():
    """验证登记身份及前置摘要，修改副本文本后立即恢复，报告不包含私人绝对路径。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', type=Path, required=True)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    workspace = args.workspace.resolve()
    metadata = json.loads((workspace.parent / (workspace.name + '.workspace.json')).read_text(encoding='utf-8'))
    source = Path(metadata['source']).resolve()
    if (metadata['status'] != 'VERIFIED' or metadata['workspace'] != str(workspace)
            or digest(args.manifest) != metadata['manifest_sha256']
            or workspace.is_relative_to(source) or source.is_relative_to(workspace)):
        raise ValueError('An independent verified registered copy is required')
    output = args.output.resolve()
    if output.is_relative_to(source) or output.is_relative_to(workspace) or output.exists():
        raise ValueError('A new report outside both client trees is required')
    record = next(row for row in json.loads(args.manifest.read_text(encoding='utf-8')) if row['path'] == 'TCLS/version.ini')
    original = checked_path(source, record['path'])
    target = checked_path(workspace, record['path'])
    if digest(original) != record['sha256'] or digest(target) != record['sha256']:
        raise ValueError('Both files must match the captured baseline before the drill')
    changed = False
    try:
        with target.open('ab') as stream:
            stream.write(b'\r\n; NZ_WORK_COPY_RECOVERY_DRILL\r\n')
        changed = digest(target) != record['sha256']
    finally:
        # 即使验证过程异常，也只从摘要正确的原件恢复副本，不回写原件。
        copy_verified(original, target, record)
    report = {'kind': 'ACTUAL_WORK_COPY_RECOVERY_NOT_WINDOWS_SNAPSHOT', 'path': record['path'],
              'baseline_sha256': record['sha256'], 'work_copy_change_observed': changed,
              'work_copy_restored': digest(target) == record['sha256'],
              'original_unchanged': digest(original) == record['sha256'], 'original_write_operations': 0}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report))


if __name__ == '__main__':
    main()
