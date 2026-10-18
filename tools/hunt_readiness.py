"""从当前恢复副本重新盘点全部逻辑图和猎场候选；静态资源存在不等于原生可玩。"""
import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import re

from tools.client_catalog import decode_resource, fields, parse_ini
from tools.client_workspace import checked_path, digest


def inspect_resource(path, relative):
    """流式核对一个当前地图文件，识别全零/空文件并保留摘要；读取期间变化即拒绝。"""
    before = path.stat()
    sha = hashlib.sha256()
    nonzero = False
    with path.open('rb') as stream:
        while block := stream.read(4 * 1024 * 1024):
            sha.update(block)
            nonzero = nonzero or bool(block.strip(b'\0'))
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ValueError('MAP_CHANGED_DURING_READ')
    return {'path': relative, 'bytes': after.st_size, 'sha256': sha.hexdigest(),
            'status': 'PRESENT_UNVERIFIED' if nonzero else 'ZERO_FILLED' if after.st_size else 'EMPTY'}


def build(workspace):
    """只读当前配置和猎场主图；保留所有逻辑图，重复 ID 拒绝，不采用旧副本的损坏状态。"""
    workspace = workspace.resolve(strict=True)
    source = 'TGame/Config/DefaultGame.ini'
    config = checked_path(workspace, source)
    config_hash = digest(config)
    text, decoding = decode_resource(config.read_bytes())
    records = parse_ini(text, source)
    prefixes = defaultdict(set)
    for record in records:
        value = fields(record.get('value'))
        if record.get('key') == 'DefaultMapPrefixes' and value.get('Prefix'):
            prefixes[str(value['Prefix']).casefold()].add(str(value.get('GameType', '')))

    # 枚举真实副本的文件名，而不是使用修复前 catalog 中的 ZERO_FILLED/MISSING 状态。
    resources = defaultdict(list)
    content = checked_path(workspace, 'TGame/CookedPC')
    for path in content.rglob('*'):
        if path.suffix.lower() in ('.udk', '.umap'):
            relative = path.relative_to(workspace).as_posix()
            path = checked_path(workspace, relative)
            if path.is_file():
                resources[path.stem.casefold()].append((path, relative))
    rows = []
    identifiers = set()
    for record in records:
        if record.get('key') != 'GameMapSettings':
            continue
        value = fields(record['value'])
        map_id = value.get('MapID', value.get('MapId', value.get('nMapID')))
        if type(map_id) is not int or map_id in identifiers:
            raise ValueError('MISSING_OR_DUPLICATE_LOGICAL_MAP_ID')
        identifiers.add(map_id)
        name = value.get('MapName', value.get('MapFileName'))
        classes = sorted(prefixes.get(str(name).split('-', 1)[0].casefold(), []))
        candidate = any(re.fullmatch(r'TGAD\d*Game\.\w+', cls) for cls in classes)
        main = [inspect_resource(path, relative) for path, relative in sorted(resources.get(str(name).casefold(), []))] if candidate else []
        rows.append({'map_id': map_id, 'name': value.get('NickName', value.get('MapDisplayName')),
                     'map_name': name, 'record_id': record['id'], 'source_path': source,
                     'scope_status': 'M6_CANDIDATE' if candidate else 'UNCLASSIFIED_RETAINED',
                     'candidate_mode_classes': classes, 'classification_basis': 'CURRENT_CONFIG_PREFIX_CLASS_ONLY',
                     'difficulty': value.get('Difficulty', value.get('DifficultyLevel')),
                     'player_limit': value.get('MaxPlayers', value.get('PlayerNum')),
                     'main_resources': main,
                     'main_resource_status': ('PRESENT_UNVERIFIED' if any(r['status'] == 'PRESENT_UNVERIFIED' for r in main)
                                              else 'UNUSABLE' if main else 'MISSING') if candidate else 'NOT_INSPECTED',
                     'dependency_closure': 'UNVERIFIED', 'native_loaded': False,
                     'specification': 'PENDING', 'compatibility_rules_version': None,
                     'single_client_verified': False, 'multiplayer_verified': False, 'enabled': False})
    if not rows or digest(config) != config_hash:
        raise ValueError('EMPTY_OR_CHANGED_MAP_CONFIG')
    rows.sort(key=lambda row: row['map_id'])
    candidates = [row for row in rows if row['scope_status'] == 'M6_CANDIDATE']
    frozen = json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')
    return {'schema_version': 1, 'kind': 'CURRENT_M6_STATIC_READINESS_NOT_LIVE',
            'source': {'path': source, 'sha256': config_hash, 'decoded_sha256': decoding['decoded_sha256']},
            'snapshot_sha256': hashlib.sha256(frozen).hexdigest(), 'all_logical_maps': len(rows),
            'm6_candidates': len(candidates), 'candidate_resource_counts': dict(Counter(row['main_resource_status'] for row in candidates)),
            'scope_confirmed_complete': False, 'first_map': None,
            'selection_order': ['DEPENDENCY_CLOSURE_VERIFIED', 'NATIVE_LOAD_VERIFIED', 'SPECIFICATION_COMPLETENESS', 'MAP_ID_ASC'],
            'delivery_policy': 'EXPLICIT_VERSIONED_PLAYABLE_COMPATIBILITY_ALLOWED',
            'client_writes': 0, 'maps': rows}


def main():
    """只向新的私人证据文件输出矩阵，不改变任何客户端配置或业务运行目录。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    output = args.output.resolve()
    if not output.is_relative_to(root / 'evidence/private') or output.is_relative_to(args.workspace.resolve()):
        raise ValueError('M6_OUTPUT_REQUIRES_PRIVATE_NON_CLIENT_PATH')
    # 必须先拒绝已有文件，不能在长时读取后意外覆盖其他批次证据。
    if output.exists():
        raise ValueError('M6_OUTPUT_EXISTS')
    result = build(args.workspace)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('x', encoding='utf-8') as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
    print(json.dumps({key: value for key, value in result.items() if key != 'maps'}, ensure_ascii=False))


if __name__ == '__main__':
    main()
