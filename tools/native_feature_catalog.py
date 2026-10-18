"""只读当前恢复副本，导出可审查的物品操作与商城元数据；不复制脚本或修改客户端。"""
import argparse
import hashlib
import json
from pathlib import Path

from tools.client_catalog import decode_resource, parse_ini, fields
from tools.native_compat_catalog import SOURCE_SHA256

VERSION = 'cn2020-feature-catalog-v1'


def extract(root):
    """固定物品目录身份，提取逐项能力、皮肤/吞噬关系及价格原值；未知语义保留为待核验。"""
    sources = {}
    def records(name):
        """读取配置并核对前后摘要；返回解析记录，不把原始客户端文本放进公开产物。"""
        path = root / 'TGame/Config' / name
        raw = path.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        if name == 'DefaultItemLibrary.ini' and digest != SOURCE_SHA256:
            raise ValueError('CURRENT_ITEM_LIBRARY_MISMATCH')
        content, _ = decode_resource(raw)
        result = parse_ini(content, name)
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError('SOURCE_CHANGED')
        sources[name] = digest
        return result

    items, skins, exchange_cards, raw_actions = {}, [], [], {}
    flags = {'bCanDeleteItem': 'delete', 'bCanDonate': 'donate', 'bCanUpgrade': 'upgrade',
             'bCanReBorn': 'reborn', 'bCanDisassemble': 'disassemble', 'bCanExchange': 'exchange',
             'bCanRepay': 'repay', 'bCanUnequip': 'unequip'}
    for record in records('DefaultItemLibrary.ini'):
        value = fields(record.get('value'))
        if record.get('key') == 'ItemDatas':
            key = str(value['nItemID'])
            if key in items:
                raise ValueError('DUPLICATE_ITEM')
            items[key] = {'item_id': value['nItemID'], 'name': value.get('ItemChineseName', value.get('ItemName', key)),
                'location': value.get('Location'), 'consume_type': value.get('ConsumeType'),
                'stack_raw': value.get('StackNum'), 'hidden': bool(value.get('bHidden')),
                'exclusive': bool(value.get('bHasUniqueness')), 'durability_max': value.get('DurableMax', 0),
                'actions': sorted(action for flag, action in flags.items() if value.get(flag)),
                'decompose_type': value.get('nDecomPoseType', 0), 'external_use': bool(value.get('FragmentExchangeUrl') or value.get('ShowURL'))}
        elif record.get('key') == 'WeaponSkinItemDatas':
            skins.append({'weapon': value['nWeaponItemId'], 'skin': value['nSkinItemId']})
        elif record.get('key') == 'ExchargeCardDatas':
            exchange_cards.append(value)
        elif value and record.get('key') not in ('WeaponPreviewMapList',):
            raw_actions.setdefault(record.get('key'), []).append(value)
    if len(items) != 4323:
        raise ValueError('CURRENT_ITEM_COUNT_MISMATCH')
    swallow = []
    for record in records('DefaultGame.ini'):
        if record.get('key') == 'SwallowPetMaps':
            value = fields(record['value'])
            swallow.append({'host': value['HostPetItemID'], 'material': value['PetItemID'],
                            'parameters': {k:v for k,v in value.items() if k.startswith('Param')}})
    offers = {}
    for record in records('DefaultCommodityLibrary.ini'):
        value = fields(record.get('value'))
        if 'nCommodityID' not in value:
            continue
        key = str(value['nCommodityID'])
        offers[key] = {k:value.get(k) for k in ('nCommodityID','sItemName','sCommodityType','nPriceArray',
            'nAvailPreiodArray','PeriodArrayForSuite','nItemIDArray','bIsPackage','bStackProp','bGiveAndAskable',
            'nBuyGrade','nBuyLimitedNum','bShowCommodity','nSaleTag','ItemOnSaleDate','ItemOnSaleTime',
            'ItemRecallDate','ItemRecallTime','nCommonRebate','nMemberRebate','MoneyType')}
    lottery = {}
    for record in records('DefaultLotteryLibrary.ini'):
        if record.get('value') is not None and record.get('key'):
            lottery.setdefault(record['key'], []).append(record['value'])
    return {'version': VERSION, 'sources': sources, 'items': items, 'skins': skins,
            'swallow': swallow, 'exchange_cards': exchange_cards, 'item_rules': raw_actions,
            'offers': offers, 'lottery': lottery, 'evidence': 'CURRENT_CONFIG_NOT_LIVE_ACCEPTANCE'}


def main():
    """显式指定恢复副本及新输出文件；已有产物拒绝覆盖，摘要用于后端固定目录。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = extract(args.workspace.resolve(strict=True))
    raw = (json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(',', ':'))+'\n').encode('utf-8')
    with args.output.open('xb') as stream:
        stream.write(raw)
    print(json.dumps({'items':len(result['items']), 'offers':len(result['offers']),
                      'sha256':hashlib.sha256(raw).hexdigest()}))


if __name__ == '__main__':
    main()
