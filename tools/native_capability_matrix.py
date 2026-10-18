"""生成4323项逐物品操作核查表；目录信号、业务实现和原生实测分别记录，不写玩家资产。

只读已冻结目录与当前桥接白名单。没有入口信号的物品也保留一行，隐藏组件不省略。
本表是后续逐入口验收基线；配置和代码检查不能自动变成界面或战斗通过。
"""
import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path

from revival.catalogs import native_inventory_manifest, native_equipment_available, NATIVE_INVENTORY_VERSION
from revival.native_features_catalog import feature_catalog, crafting_catalog, exchange_catalog, engineering_catalog, avatar_catalog, counter_item_ids, FRAGMENTS, FEATURE_VERSION


def build():
    """将每个目录ID关联到操作、原生请求、规则与持久表，输出可检索的完整静态台账。"""
    from tools.native_feature_bridge import NativeFeatureBridge
    catalog, crafting = feature_catalog(), crafting_catalog()
    # 台账独立于Django进程读取同一固定目录；不得从可变客户端工作文件推测维修价格。
    raw=(Path(__file__).resolve().parents[1]/'config/cn2020-item-maintenance-v1.json').read_bytes()
    if hashlib.sha256(raw).hexdigest()!='b033a5e1ad58388505ccee8bc9c18b6df64085fbfdd9d54b308fc441ab852513':
        raise ValueError('MAINTENANCE_CATALOG_MISMATCH')
    maintenance=json.loads(raw)
    definitions = {row['item_id']: row for row in native_inventory_manifest()['items']}
    if set(map(int, catalog['items'])) != set(definitions) or len(definitions) != 4323:
        raise ValueError('CAPABILITY_CATALOG_COVERAGE_MISMATCH')
    # 描述当前代码边界，不因HTTP动作名称存在就判定其逐物品规则已经完成。
    operations = {
        'equip': dict(action='/api/game/inventory/equip', requests=['A008'], rule=NATIVE_INVENTORY_VERSION,
            persistence=['equipment', 'native_inventory_bindings'], implementation='PER_ITEM_CHECK_REQUIRED'),
        'unequip': dict(action='/api/game/inventory/equip', requests=['A008'], rule=NATIVE_INVENTORY_VERSION,
            persistence=['equipment'], implementation='IMPLEMENTED_FOR_OWN_EQUIPPED_INSTANCE'),
        'delete': dict(action='inventory.delete', requests=['A167'], rule=FEATURE_VERSION,
            persistence=['inventory', 'asset_operations', 'asset_entries'], implementation='IMPLEMENTED'),
        'skin': dict(action='inventory.skin', requests=['37A2'], rule=FEATURE_VERSION,
            persistence=['native_item_states'], implementation='IMPLEMENTED_PACKET_CAPACITY_REVIEW_PENDING'),
        'swallow': dict(action='inventory.swallow / inventory.unswallow', requests=['3932'], rule=FEATURE_VERSION,
            persistence=['native_item_states'], implementation='IMPLEMENTED_REVERSIBLE_RELATION'),
        'use': dict(action='inventory.use', requests=[], rule='item:<id>:use published revision',
            persistence=['native_rule_revisions', 'inventory', 'native_item_states'], implementation='ITEM_RULE_AND_NATIVE_MAPPING_PENDING'),
        'fragment_draw': dict(action='draw.commit / draw.confirm', requests=['FA0C', 'FA0E', 'FA1A'], rule='cn2020-draw-compat-v2',
            persistence=['native_receipts', 'native_draw_selections', 'inventory'], implementation='IMPLEMENTED'),
        'workshop_draw': dict(action='workshop.draw / workshop.confirm', requests=['FA20','FA23'], rule='cn2020-draw-compat-v3',
            persistence=['native_receipts','native_workshop_states','inventory'], implementation='IMPLEMENTED_DEFAULT_COST_LIVE_VERIFIED_CUSTOM_COST_UI_PENDING'),
        'upgrade': dict(action='inventory.craft', requests=['A375'], rule=exchange_catalog()['version'],
            persistence=['inventory','native_inventory_bindings','asset_operations','asset_entries'],
            implementation='139_RECIPES_MYSQL_VERIFIED_ONE_RECIPE_LIVE_VERIFIED'),
        'engineer_decompose': dict(action='engineering.decompose', requests=['F402'], rule='cn2020-engineering-compat-v1',
            persistence=['inventory','native_receipts','asset_operations','asset_entries'], implementation='MYSQL_AND_DLL_VERIFIED_LIVE_PENDING'),
        'engineer_compose': dict(action='engineering.compose', requests=['F404'], rule='cn2020-engineering-compat-v1',
            persistence=['inventory','native_receipts','native_engineering_progress'], implementation='37_RECIPES_MYSQL_VERIFIED_RECIPE_90037_NORMAL_AND_ADVANCED_LIVE_VERIFIED'),
        'material_compose': dict(action='engineering.material_compose', requests=['F408'], rule='cn2020-engineering-compat-v1',
            persistence=['inventory','native_receipts'], implementation='MYSQL_AND_DLL_VERIFIED_LIVE_PENDING'),
        'material_decompose': dict(action='engineering.material_decompose', requests=['F406'], rule='cn2020-engineering-compat-v1',
            persistence=['inventory','native_receipts'], implementation='MYSQL_AND_DLL_VERIFIED_LIVE_PENDING'),
        'shop': dict(action='shop.purchase / shop.gift / shop.renew', requests=['A505'], rule=FEATURE_VERSION,
            persistence=['native_receipts', 'inventory', 'wallets'], implementation='BASE_TRANSACTION_IMPLEMENTED_OFFER_CONDITIONS_PENDING'),
        'repair': dict(action='inventory.repair',requests=['A50B'],rule=maintenance['version'],
            persistence=['native_item_states','native_receipts','wallets'],implementation='MYSQL_AND_DLL_VERIFIED_LIVE_PENDING'),
        'sell': dict(action='inventory.sell',requests=['A363'],rule=maintenance['version'],
            persistence=['inventory','native_receipts','wallets'],implementation='MYSQL_AND_DLL_VERIFIED_LIVE_PENDING'),
        'renew': dict(action='shop.renew',requests=['A509'],rule=maintenance['version'],
            persistence=['inventory','native_receipts','wallets'],implementation='SINGLE_INSTANCE_IMPLEMENTED_PACKAGE_RENEW_PENDING'),
        'donate': dict(action=None, requests=['A363'], rule=None,
            persistence=[], implementation='SEMANTICS_AND_IMPLEMENTATION_PENDING'),
    }
    for action, requests in [('reborn', []), ('disassemble', ['F402']),
                             ('exchange', ['A371']), ('repay', [])]:
        operations[action] = dict(action='inventory.'+action, requests=requests,
            rule='item:<id>:'+action+' published revision; cn2020-crafting-v1',
            persistence=['native_rule_revisions', 'inventory', 'native_item_states'],
            implementation='GENERIC_ENGINE_ONLY_ITEM_RULE_AND_NATIVE_MAPPING_PENDING')
    for operation in operations.values():
        operation['enabled_native_requests'] = [code for code in operation['requests']
            if code == 'A008' or int(code, 16) in NativeFeatureBridge.COMMANDS]
        operation['ui_result'] = 'NOT_VERIFIED_IN_CURRENT_SESSION'
        operation['combat_result'] = 'NOT_VERIFIED'
        operation['transaction'] = 'MYSQL_WITH_ASSET_LEDGER_AUDIT_OUTBOX' if operation['persistence'] else None

    offers, recipes = defaultdict(list), defaultdict(list)
    for key, offer in catalog['offers'].items():
        for item_id in offer['nItemIDArray'] or []:
            offers[item_id].append(int(key))
    for index, recipe in enumerate(crafting['upgrades']):
        for role in ('ExchangeItemId', 'CostItemId', 'AchieveItemId'):
            recipes[recipe[role]].append({'index': index, 'role': role, 'type': recipe['UpgradeType']})
    skins, swallow = defaultdict(list), defaultdict(list)
    for relation in catalog['skins']:
        for role in ('weapon', 'skin'):
            skins[relation[role]].append(relation)
    for relation in catalog['swallow']:
        for role in ('host', 'material'):
            swallow[relation[role]].append(relation)
    rows = []
    for item_id in sorted(definitions):
        item, definition = catalog['items'][str(item_id)], definitions[item_id]
        actions = set(item['actions'])
        upkeep=maintenance['items'][str(item_id)]
        if upkeep['ConsumeType']==2 and upkeep['RepairType']==2:
            actions.add('repair')
        if upkeep['bDroppable'] and not upkeep['bDefaultItem'] and upkeep['ConsumeType'] in (0,2):
            actions.add('sell')
        if upkeep['ConsumeType']==1 and (upkeep['nRepairPrice'] or upkeep['nRepairPrice1']):
            actions.add('renew')
        if item['location'] is not None and item['location'] >= 0:
            actions.add('equip')
        if any(r['weapon'] == item_id for r in skins[item_id]):
            actions.add('skin')
        if any(r['host'] == item_id for r in swallow[item_id]):
            actions.add('swallow')
        if item_id in FRAGMENTS.values():
            actions.add('fragment_draw')
        if item_id in (103745,103746):
            # 两种工坊券与旧155条材料兑换配方是不同入口，不用配方武器ID冒充券。
            actions.add('workshop_draw')
        if item['decompose_type'] in (1,2,3):
            # 工程分解不使用bCanDisassemble菜单旗标；按nDecomPoseType单独列出，避免漏掉268件物品。
            actions.add('engineer_decompose')
        if any(r['nItemID']==item_id for r in engineering_catalog()['recipes']) or item_id in (100465,100474,100542,100543):
            actions.add('engineer_compose')
        if item_id in (100764,100765,100766):
            actions.add('material_compose')
        if item_id in (100765,100766,100767):
            actions.add('material_decompose')
        if item['external_use'] or item_id in FRAGMENTS.values():
            actions.add('use')
        if offers[item_id]:
            actions.add('shop')
        if any(r['role'] == 'ExchangeItemId' for r in recipes[item_id]):
            actions.add('upgrade')
        unknown = actions-set(operations)
        if unknown:
            raise ValueError('UNCLASSIFIED_CATALOG_ACTION: '+','.join(sorted(unknown)))
        rows.append(dict(item_id=item_id, name=item['name'], hidden=item['hidden'],
            location=item['location'], consume_type=item['consume_type'],
            storage='counter' if item_id in counter_item_ids() else 'instance',
            configured_exclusive=item['exclusive'], effective_exclusive=False,
            exclusion_rule='repeat instances allowed; source flag semantics pending',
            configured_actions=item['actions'], operations=sorted(actions),
            equipment_enabled=native_equipment_available(definition),
            avatar_component=avatar_catalog()['components'].get(str(item_id)),
            avatar_role=avatar_catalog()['roles'].get(str(item_id)),
            # 当前分页桥已发送全部有效物品类型；0/2限制只属于旧非分页接口。
            # 此处仅描述静态可投影范围，不证明每种期限/材料/分期语义已经通过客户端验收。
            login_projection=True,
            legacy_login_projection=item['consume_type'] in (0, 2),
            shop_offer_ids=sorted(offers[item_id]), crafting_references=recipes[item_id],
            maintenance=upkeep,
            skin_relations=skins[item_id], swallow_relations=swallow[item_id],
            entry_review='PENDING_LIVE_AND_HIDDEN_UI_REVIEW'))
    counts = Counter(action for row in rows for action in row['operations'])
    return dict(version='cn2020-capability-audit-v4', evidence='STATIC_COVERAGE_NOT_FEATURE_ACCEPTANCE',
        sources=dict(catalog['sources'], **crafting['sources']), operations=operations,
        summary=dict(items=len(rows), hidden=sum(row['hidden'] for row in rows),
            login_projected=sum(row['login_projection'] for row in rows),
            legacy_login_projected=sum(row['legacy_login_projection'] for row in rows),
            equipment_enabled=sum(row['equipment_enabled'] for row in rows), operations=dict(counts),
            current_session_ui_passed=0, combat_passed=0), items=rows)


def main():
    """生成新JSON证据与简短摘要，拒绝覆盖旧核查记录；不连接数据库和客户端进程。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    report = build()
    raw = (json.dumps(report, ensure_ascii=False, sort_keys=True, separators=(',', ':'))+'\n').encode('utf-8')
    with args.output.open('xb') as stream:
        stream.write(raw)
    print(json.dumps(dict(report['summary'], sha256=hashlib.sha256(raw).hexdigest())))


if __name__ == '__main__':
    main()
