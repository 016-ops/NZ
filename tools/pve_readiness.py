"""将已捕获目录转为逐地图 PvE 准备矩阵；保留全部未知项，不把前缀匹配当运行验证。"""
import argparse
import hashlib
import json
from pathlib import Path


def fields(record):
    """保留结构化字段的最后值供索引，原始 record_id 仍指向完整重复字段证据。"""
    return {field['key']: field['value'] for field in record.get('attributes', {}).get('fields', [])}


def build(catalog):
    """关联逻辑图、物理主图、同目录线索和模式类，依赖闭包与难度始终单独标未知。"""
    load = lambda name: json.loads((catalog / 'json' / (name + '.json')).read_text(encoding='utf-8'))
    files, maps, modes = load('source_files'), load('maps'), load('modes')
    by_stem = {}
    for file in files:
        if file['extension'].lower() == '.udk':
            by_stem.setdefault(Path(file['path']).stem.casefold(), []).append(file)
    prefixes = {}
    for mode in modes:
        if mode.get('prefix'):
            prefixes.setdefault(mode['prefix'].casefold(), []).append(mode)
    rows = []
    for record in maps:
        if record.get('logical_map_id') is None:
            continue
        values = fields(record)
        name = values.get('MapName')
        sources = by_stem.get(str(name).casefold(), [])
        prefix = str(name).split('-', 1)[0].casefold()
        possible_modes = prefixes.get(prefix, [])
        candidate = prefix in ('sv', 'sv3', 'sv4', 'if', 'if2') or record['logical_map_id'] == 16
        nearby = []
        for source in sources:
            parent = str(Path(source['path']).parent).replace('\\', '/')
            nearby.extend({'path': file['path'], 'status': file['status'], 'sha256': file['sha256']}
                          for file in files if file['path'].startswith(parent + '/') and file['status'] == 'ZERO_FILLED')
        rows.append({'map_id': record['logical_map_id'], 'name': values.get('NickName'), 'map_name': name,
                     'record_id': record.get('record_id'), 'source_path': record.get('source_path'),
                     'scope_status': 'M5_CANDIDATE' if candidate else 'UNCLASSIFIED_RETAINED',
                     'candidate_basis': 'LOCAL_PREFIX_OR_EXISTING_STEEL_FOREST_SEED' if candidate else None,
                     'candidate_mode_classes': sorted({mode['class_name'] for mode in possible_modes}),
                     'main_resources': [{'path': source['path'], 'sha256': source['sha256'], 'status': source['status']} for source in sources],
                     'main_resource_status': 'MISSING' if not sources else 'ZERO_FILLED' if all(source['status'] == 'ZERO_FILLED' for source in sources) else 'PRESENT_UNVERIFIED',
                     'nearby_zero_resource_clues': nearby, 'dependency_closure': 'UNVERIFIED',
                     'difficulty': record.get('difficulty'), 'player_limit': record.get('player_limit'),
                     'native_loaded': False, 'single_client_verified': False, 'multiplayer_verified': False,
                     'waves_boss_rewards': 'UNVERIFIED', 'enabled': False})
    rows.sort(key=lambda row: row['map_id'])
    frozen = json.dumps(rows, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()
    return {'schema_version': 1, 'snapshot_sha256': hashlib.sha256(frozen).hexdigest(), 'all_logical_maps': len(rows),
            'm5_candidates': sum(row['scope_status'] == 'M5_CANDIDATE' for row in rows),
            'scope_confirmed_complete': False, 'method': 'CAPTURED_CATALOG_STATIC_ASSOCIATION_NOT_RUNTIME_DEPENDENCY_PROOF', 'maps': rows}


def main():
    """输出到新的客户端外目录；完整私人路径不自动进入公开交接。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--catalog', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--client-root', type=Path, default=Path(__file__).resolve().parents[2] / 'nz')
    args = parser.parse_args()
    if args.output.resolve().is_relative_to(args.client_root.resolve()):
        raise ValueError('Readiness output must be outside the original client')
    report = build(args.catalog)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x', encoding='utf-8') as output:
        json.dump(report, output, ensure_ascii=False, indent=2)
    print(json.dumps({key: value for key, value in report.items() if key != 'maps'}))


if __name__ == '__main__':
    main()
