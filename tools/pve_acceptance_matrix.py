"""逐一保留当前四类 PvE 地图及未归类逻辑图；静态清单不产生任何实机通过结果。"""
import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import re

from tools.client_catalog import decode_resource, fields, parse_ini
from tools.client_workspace import checked_path, digest
from tools.hunt_readiness import inspect_resource
from tools.native_map_streaming import inspect_streaming


def classify(map_name, configured_classes):
    """按当前配置的精确玩法类归类，覆盖旧ATD与新版TGATD；名称候选不当作规则确认。"""
    modes = set()
    for cls in configured_classes:
        if re.fullmatch(r'TGAD\d*Game\.\w+', cls):
            modes.add('hunt')
        # 早期11张塔防图使用ATDGameInfo，不能因类名不含TG而从验收分母中丢失。
        if cls == 'ATDGame.ATDGameInfo' or re.fullmatch(r'TGATD\d*Game\.\w+', cls):
            modes.add('tower_defense')
        if re.fullmatch(r'(?:PVEGame|TGSV\d*Game)\.TGSV\d*Game', cls):
            modes.add('survival')
        if cls == 'PVEGame.TGIFGame' or re.fullmatch(r'TGIF\d*Game\.\w+', cls):
            modes.add('defense')
    if not modes and re.match(r'^IF\d*-', map_name, re.IGNORECASE):
        return ['defense'], 'MAP_NAME_CANDIDATE_REQUIRES_CLASS_CONFIRMATION'
    return sorted(modes), 'CURRENT_CONFIG_PREFIX_CLASS' if modes else 'UNCLASSIFIED_RETAINED'


def difficulty_catalog(workspace):
    """读取原生全局难度名称表；只描述子模式ID，不推定每张地图开放了全部难度。"""
    relative='TGame/Config/DefaultEngine.ini'
    path=checked_path(workspace,relative)
    if not path.is_file():
        return dict(status='SOURCE_MISSING',source=relative,entries=[],map_applicability='PENDING_PER_MAP')
    before=digest(path)
    text,decoded=decode_resource(path.read_bytes())
    matches=[fields(row.get('value')) for row in parse_ini(text,relative)
             if row.get('section')=='UTGame.TGOnlineLogic' and row.get('key')=='TGPVESubModeTypeMappings']
    if len(matches)!=1:
        raise ValueError('PVE_DIFFICULTY_MAPPING_NOT_UNIQUE')
    names,values=matches[0].get('SubModeTypeNames'),matches[0].get('SubModeTypeValues')
    # 显式拒绝截断、重复和错误字段，避免将未知格式变成假难度或静默减少验收范围。
    if (not isinstance(names,list) or not isinstance(values,list) or not 1<=len(names)<=32
            or len(names)!=len(values) or any(not isinstance(name,str) or not name for name in names)
            or any(type(value) is not int or not 0<value<65536 for value in values)
            or len(set(values))!=len(values) or digest(path)!=before):
        raise ValueError('PVE_DIFFICULTY_MAPPING_INVALID_OR_CHANGED')
    return dict(status='GLOBAL_NAMES_VERIFIED',source=relative,sha256=before,
                decoded_sha256=decoded['decoded_sha256'],map_applicability='PENDING_PER_MAP',
                entries=[dict(submode_id=value,name=name) for name,value in zip(names,values)])


def build(workspace):
    """独立读取全部 248 条逻辑地图；每张候选主图检查存在/摘要，不合并同名地图 ID 或跳过隐藏入口。"""
    workspace = Path(workspace).resolve(strict=True)
    relative = 'TGame/Config/DefaultGame.ini'
    config = checked_path(workspace, relative)
    before = digest(config)
    text, decoded = decode_resource(config.read_bytes())
    records = parse_ini(text, relative)
    prefixes = defaultdict(set)
    mode_ids = defaultdict(set)
    for record in records:
        value = fields(record.get('value'))
        if record.get('key') == 'DefaultMapPrefixes' and value.get('Prefix'):
            prefixes[value['Prefix'].casefold()].add(value.get('GameType', ''))
        if record.get('key') == 'GameModeSettings' and value.get('ModeName'):
            mode_ids[value['ModeName']].add(value.get('ModeId'))
    lookup,package_lookup = defaultdict(list),defaultdict(list)
    for path in checked_path(workspace, 'TGame/CookedPC').rglob('*'):
        if path.is_file():
            if path.suffix.casefold() in ('.udk','.umap','.upk','.u'):
                package_lookup[path.stem.casefold()].append(path)
            if path.suffix.casefold() in ('.udk','.umap'):
                lookup[path.stem.casefold()].append(path)
    maps = []
    seen = set()
    for record in records:
        if record.get('key') != 'GameMapSettings':
            continue
        value = fields(record['value'])
        map_id, name = value.get('MapId'), value.get('MapName')
        if type(map_id) is not int or map_id in seen or not isinstance(name, str):
            raise ValueError('MAP_ID_OR_RESOURCE_CONFLICT')
        seen.add(map_id)
        classes = sorted(prefixes.get(name.split('-', 1)[0].casefold(), set()))
        modes, basis = classify(name, classes)
        resources = [inspect_resource(path, path.relative_to(workspace).as_posix())
                     for path in sorted(lookup.get(name.casefold(), []))] if modes else []
        streaming_resources = []
        for path in sorted(lookup.get(name.casefold(), [])) if modes else []:
            # 动态流送关卡不一定存在于普通导入表中；逐图保留未知布局和失败，不以空依赖替代。
            relative_map = path.relative_to(workspace).as_posix()
            try:
                dynamic = inspect_streaming(path)
                references = []
                for package in dynamic['streaming_packages']:
                    matches = package_lookup.get(package.casefold(), [])
                    references.append(dict(package=package,
                        status='MISSING' if not matches else 'AMBIGUOUS' if len(matches) != 1 else 'PRESENT_UNVERIFIED',
                        paths=sorted(candidate.relative_to(workspace).as_posix() for candidate in matches)))
                dynamic['streaming_references'] = references
            except (ValueError, OSError) as error:
                dynamic = dict(status='UNVERIFIED', error=str(error), dependency_closure_verified=False,
                               native_loaded=False)
            streaming_resources.append(dict(path=relative_map, **dynamic))
        maps.append(dict(map_id=map_id, name=value.get('NickName'), map_name=name,
            modes=modes, classification_basis=basis, configured_classes=classes,
            configured_mode_ids=sorted({i for cls in classes for i in mode_ids[cls] if type(i) is int}),
            main_resources=resources,
            streaming_resources=streaming_resources,
            main_resource_status=('PRESENT_UNVERIFIED' if any(r['status'] == 'PRESENT_UNVERIFIED' for r in resources)
                                  else 'MISSING_OR_UNUSABLE') if modes else 'NOT_INSPECTED',
            dependency_closure='PENDING', difficulty_rules='PENDING_PER_MAP',
            acceptance=dict(world='PENDING', movement='PENDING', loadout_effects='PENDING',
                objectives='PENDING', enemies_and_boss='PENDING', victory_and_defeat='PENDING',
                settlement='PENDING', return_to_lobby='PENDING', next_game='PENDING',
                multiplayer='PENDING', cross_network='PENDING', two_rooms='PENDING', ten_games='PENDING'),
            live_evidence=[], playable_accepted=False))
    if not maps or digest(config) != before:
        raise ValueError('MAP_SOURCE_CHANGED')
    maps.sort(key=lambda row: row['map_id'])
    counts = Counter(mode for row in maps for mode in row['modes'])
    return dict(schema_version=3, kind='FOUR_MODE_MAP_INVENTORY_NOT_LIVE_ACCEPTANCE',
        source=dict(path=relative, sha256=before, decoded_sha256=decoded['decoded_sha256']),
        global_difficulty_names=difficulty_catalog(workspace),
        scope_confirmed_complete=False, all_logical_maps=len(maps), mode_counts=dict(counts),
        unclassified_retained=sum(not row['modes'] for row in maps),
        client_writes=0, accepted_maps=0, maps=maps)


def main():
    """只创建新的私人证据文件；后续实机记录逐图追加，不能覆盖历史失败或据此启用玩法。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    output = args.output.resolve()
    if not output.is_relative_to(root / 'evidence/private') or output.is_relative_to(args.workspace.resolve()):
        raise ValueError('PRIVATE_NON_CLIENT_OUTPUT_REQUIRED')
    if output.exists():
        raise ValueError('MATRIX_OUTPUT_EXISTS')
    result = build(args.workspace)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('x', encoding='utf-8') as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
    print(json.dumps({k: v for k, v in result.items() if k != 'maps'}, ensure_ascii=True))


if __name__ == '__main__':
    main()
