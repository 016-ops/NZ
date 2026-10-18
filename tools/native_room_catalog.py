"""读取固定当前配置，建立55张PvE地图的频道房间规则；MapIndex不能误当成MapId。"""
import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path
from .client_catalog import decode_resource,parse_ini,fields
from .client_workspace import checked_path,digest
from .pve_acceptance_matrix import classify

SOURCES={'DefaultGame.ini':'1d970d31f82cd0d38c2386d9bda0e1928917141776fd0ff67b0342ea9525c34d',
         'DefaultEngine.ini':'575574e060fad4d529eda1ee53e00e67db77746144d070bd9fa8cc3dc3bded1b'}
VERSION='cn2020-native-room-compat-v2'


def extract(workspace):
    """保留原地图、人数和难度索引；缺失菜单映射使用明确父模式兼容规则，缺主图仍不可分配。"""
    workspace=Path(workspace).resolve(strict=True);records={}
    for name,expected in SOURCES.items():
        path=checked_path(workspace,'TGame/Config/'+name)
        raw=path.read_bytes()
        if hashlib.sha256(raw).hexdigest()!=expected:raise ValueError('ROOM_CONFIG_SOURCE_CHANGED')
        text,_=decode_resource(raw);records[name]=parse_ini(text,name)
    def values(name,key):
        """仅按准确键取结构，避免将展示标签或不同节的标量误当规则。"""
        return [fields(row.get('value')) for row in records[name] if row.get('key')==key]
    prefixes=defaultdict(set);mode_ids=defaultdict(set)
    for row in values('DefaultGame.ini','DefaultMapPrefixes'):prefixes[row['Prefix']].add(row['GameType'])
    for row in values('DefaultGame.ini','GameModeSettings'):mode_ids[row['ModeName']].add(row['ModeId'])
    ui_maps=values('DefaultEngine.ini','TGMapMappings')
    mode_rows=values('DefaultEngine.ini','TGMatchTypeMappings')
    counts,=values('DefaultEngine.ini','TGPlayerCountMappings')
    difficulties,=values('DefaultEngine.ini','TGPVESubModeTypeMappings')
    observer,=values('DefaultEngine.ini','TGObserverCountMappings')
    pool=difficulties['SubModeTypeValues']
    if len(pool)!=8 or len(counts['CountValues'])!=17:raise ValueError('ROOM_INDEX_TABLE_CHANGED')
    resources=defaultdict(list)
    for path in (workspace/'TGame/CookedPC').rglob('*.udk'):resources[path.stem.casefold()].append(path)
    maps={}
    for row in values('DefaultGame.ini','GameMapSettings'):
        classes=sorted(prefixes[row['MapName'].split('-',1)[0]])
        kinds,_=classify(row['MapName'],classes)
        if not kinds:continue
        ids={i for cls in classes for i in mode_ids[cls]}
        if len(ids)!=1:raise ValueError('ROOM_MODE_NOT_UNIQUE')
        mode_id=next(iter(ids));mapping=[m for m in ui_maps if m['MapId']==row['MapId']]
        if len(mapping)>1:raise ValueError('ROOM_MAP_ID_DUPLICATE')
        mapping=mapping[0] if mapping else None
        candidates=[m for m in mode_rows if m['MatchTypeId']==mode_id and
                    mapping and mapping['MapIndex'] in m.get('Maps',[])]
        basis='CURRENT_UI_MAP_INDEX_REFERENCES'
        if not candidates:
            # 旧/隐藏图仍保留在总范围；继承同实际玩法的规则并显式标版本，不能隐藏为完成。
            candidates=[m for m in mode_rows if m['MatchTypeId']==mode_id and m.get('ContainedType')==0]
            basis='COMPAT_INHERIT_MODE_FOR_UNLISTED_MAP'
        if len(candidates)>1:raise ValueError('ROOM_MODE_RULE_AMBIGUOUS')
        mode=candidates[0] if candidates else None
        if mode:
            player_counts=[counts['CountValues'][i] for i in mode['PlayerCounts']]
            indexes=mapping.get('SubModes') if mapping and mapping.get('SubModes') else mode.get('SubModes',[])
            if any(type(i) is not int or not 0<=i<len(pool) for i in indexes):raise ValueError('ROOM_DIFFICULTY_INDEX_INVALID')
            submodes=[pool[i] for i in indexes]
        else:
            # 唯一无父规则的围剿旧主图缺失；记录兼容规则，实际分配继续被主资源条件拒绝。
            player_counts=[4];submodes=[4097,4098,4099];basis='COMPAT_FOUR_PLAYER_UNLISTED_MODE'
        matches=resources[row['MapName'].casefold()]
        if len(matches)>1:raise ValueError('ROOM_MAIN_RESOURCE_AMBIGUOUS')
        resource=dict(path=matches[0].relative_to(workspace).as_posix(),bytes=matches[0].stat().st_size,
                      sha256=digest(matches[0])) if matches else None
        maps[str(row['MapId'])]=dict(map_id=row['MapId'],map_name=row['MapName'],name=row.get('NickName',''),
            mode_id=mode_id,classes=classes,kinds=kinds,ui_map_index=mapping['MapIndex'] if mapping else None,
            player_counts=player_counts,submodes=submodes,rule_basis=basis,main_resource=resource,
            native_loaded=False,combat_verified=False)
    if len(maps)!=55 or any(digest(workspace/'TGame/Config'/name)!=sha for name,sha in SOURCES.items()):
        raise ValueError('ROOM_SCOPE_OR_SOURCE_CHANGED')
    return dict(version=VERSION,sources=SOURCES,maps=maps,observer_counts=observer['CountValues'],
                seat_layout=dict(total=32,camp_stride=16,combat='HALF_CAPACITY_AT_0_AND_16',
                    observers='NON_COMBAT_SLOTS_WITH_TOTAL_CAPACITY',regular=1,rebel=0,observer=2,
                    source='GfxMovie_MultiPlayerRoom.CheckCampPlayerCount',
                    observer_none=-1,observer_a=0,observer_b=1,
                    constants_source='GfxMovie_TGBase.ScriptText:6dc303a82b8ad439f85f88be6f38f077a540054893293ae0d865119132c5b59f'),
                source_player_states=dict(unready=8,ready=9,observer=10,in_match=12,owner_flag=4),
                compatibility=dict(startup_lease_seconds=180,channel=1,main_channel=1,
                    unlisted_map_policy='INHERIT_EXACT_GAME_MODE_OR_EXPLICIT_FOUR_PLAYER_RULE',
                    game_server_selection='SERVER_ONLY_IGNORE_CLIENT_DS_ADDRESS',
                    evidence='LOBBY_RULES_NOT_NATIVE_READY_OR_ACCEPTANCE'))


def main():
    """输出新的冻结规则文件；不覆盖旧目录，不启用节点或修改客户端。"""
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();result=extract(args.workspace)
    raw=(json.dumps(result,ensure_ascii=False,sort_keys=True,separators=(',',':'))+'\n').encode('utf-8')
    with args.output.open('xb') as stream:stream.write(raw)
    print(json.dumps(dict(maps=len(result['maps']),main_resources=sum(r['main_resource'] is not None for r in result['maps'].values()),
                         sha256=hashlib.sha256(raw).hexdigest(),native_ready=False)))


if __name__=='__main__':main()
