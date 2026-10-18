"""只读提取当前猎场宝箱的原始候选与期限，生成版本化的等概率兼容规则。"""
import argparse
import copy
import hashlib
import json
from pathlib import Path

from tools.client_catalog import decode_resource, fields, parse_ini


VERSION = 'cn2020-container-compat-v2'
BASE_SHA256 = 'cb03c8516886699ce4e401782d7e21ab8e2a8dc4fc045a5444ba542b27f7bef7'
SOURCES = {
    'DefaultCommonDataLibrary.ini': '34702b2499def121c51a49a99255db6186fd12355438c81ef3a297d5381576fb',
    'DefaultItemLibrary.ini': '741963c2174004735a9456ea559a6976f53665f591f9ba995d7889b8db0911a6',
}


def extract(workspace):
    """核对两份源配置和旧规则后扩展奖池；缺失概率与分组选择语义明确采用兼容规则。

    原始 ItemIDArray 是展示分组，不能证明每组同时发奖。因此每次开箱仅从去重后的
    物品/数量/期限候选中等概率选一项；不宣称还原商业服权重或独占箱掉率。
    """
    base_raw = (Path(__file__).resolve().parents[1] / 'config/cn2020-container-compat-v1.json').read_bytes()
    if hashlib.sha256(base_raw).hexdigest() != BASE_SHA256:
        raise ValueError('CONTAINER_BASE_MISMATCH')
    result = copy.deepcopy(json.loads(base_raw))
    records = {}
    for name, digest in SOURCES.items():
        path = workspace / 'TGame/Config' / name
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != digest:
            raise ValueError('CONTAINER_SOURCE_MISMATCH:' + name)
        records[name] = parse_ini(decode_resource(raw)[0], name)
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError('CONTAINER_SOURCE_CHANGED')
    items = {v['nItemID']: v for r in records['DefaultItemLibrary.ini']
             if r['key'] == 'ItemDatas' for v in [fields(r['value'])]}
    pools = {}
    for record in records['DefaultCommonDataLibrary.ini']:
        if record['key'] != 'ADBossBoxMappings':
            continue
        mapping = fields(record['value'])
        for source in mapping['BoxArray']:
            box = fields(source)
            groups, periods = box['ItemIDArray'], box['ItemAvailTimeArray']
            if not groups or len(groups) != len(periods):
                raise ValueError('CONTAINER_POOL_LAYOUT')
            candidates = {}
            for group, period in zip(groups, periods):
                for item_id in fields(group)['ItemIDs']:
                    item = items[item_id]
                    quantity, duration = 1, None
                    if period != -1:
                        if type(period) is not int or period <= 0:
                            raise ValueError('CONTAINER_PERIOD')
                        if item['ConsumeType'] == 0 and item.get('StackNum', 0) > 0:
                            # 计数型原料的显示数值是数量，不是小时；例如圣人碎片为五个。
                            quantity = period
                        elif item['ConsumeType'] == 1 and period == 1752000:
                            # 当前角色的200年展示哨兵对应永久权益，沿用已有永久投影合同。
                            duration = None
                        elif item['ConsumeType'] == 1 and period * 3600 <= 315360000:
                            duration = period * 3600
                        else:
                            raise ValueError('UNVERIFIED_CONTAINER_PERIOD')
                    if not 1 <= quantity <= 1000:
                        raise ValueError('CONTAINER_REWARD_QUANTITY')
                    reward = dict(item_id=item_id, quantity=quantity, duration_seconds=duration)
                    key = (item_id, quantity, duration)
                    candidates[key] = reward
            pool = sorted(candidates.values(), key=lambda r: (r['item_id'], r['quantity'], r['duration_seconds'] or 0))
            name = box['sBoxName']
            if name in pools and pools[name]['pool'] != pool:
                raise ValueError('AMBIGUOUS_CONTAINER_NAME')
            stored = pools.setdefault(name, dict(pool=pool, maps=[], source=box))
            stored['maps'].append(mapping['nMapId'])
    for row in result['containers']:
        if row['name'] not in pools:
            continue
        source = pools[row['name']]
        result['recipes'][str(row['ProtocolValue'])] = dict(
            item_id=row['ChestItemID'], name=row['name'], mode='random_one',
            pool=[dict(key=str(index), weight=1, rewards=[reward])
                  for index, reward in enumerate(source['pool'])],
            source_map_ids=sorted(set(source['maps'])), source_box=source['source'],
            policy='Source candidates and counts retained; one uniformly selected candidate per box. '
                   'Display groups are flattened and deduplicated. Original probabilities are unavailable. '
                   'This is an explicit compatibility rule, not original commercial drop rates.')
    result['version'] = VERSION
    result['sources'] = dict(SOURCES)
    result['previous_version'] = json.loads(base_raw)['version']
    # 保持已发布的鼠年礼包内容不变，升级只增加有来源的猎场箱候选。
    if result['recipes']['307'] != json.loads(base_raw)['recipes']['307']:
        raise ValueError('EXISTING_CONTAINER_RECIPE_CHANGED')
    return result


def main():
    """写入调用者指定的新文件，拒绝覆盖既有规则；输出摘要和实际覆盖数量。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = extract(args.workspace.resolve(strict=True))
    raw = (json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(',', ':')) + '\n').encode('utf-8')
    with args.output.open('xb') as stream:
        stream.write(raw)
    print(json.dumps(dict(version=VERSION, recipes=len(result['recipes']),
                         pending=len(result['containers'])-len(result['recipes']),
                         sha256=hashlib.sha256(raw).hexdigest())))


if __name__ == '__main__':
    main()
