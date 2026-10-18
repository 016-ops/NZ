"""显式加载独立合成 PvE 配置；规则版本与普通合成目录分离，不写入当前客户端库。"""
import copy
import json
import hashlib
from functools import lru_cache
from pathlib import Path

NATIVE_INVENTORY_VERSION = 'cn2020-full-inventory-compat-v1'
NATIVE_EQUIPMENT_PROJECTION = 'cn2020-equipment-state-v1'
NATIVE_MANIFEST_SHA256 = '4316a6fe67f20a5e57d249fb754fac2623b6ceb8c805fb9218124178da805ea9'


@lru_cache(maxsize=1)
def native_inventory_manifest():
    """加载固定目录并核对整体摘要；版本更新必须新增文件和版本，运行中的快照不随文件变化。"""
    raw = (Path(__file__).resolve().parents[1] / 'config/cn2020-compat-inventory-v1.json').read_bytes()
    if hashlib.sha256(raw).hexdigest() != NATIVE_MANIFEST_SHA256:
        raise ValueError('Native inventory manifest fingerprint mismatch')
    result = json.loads(raw)
    if result['rules_version'] != NATIVE_INVENTORY_VERSION:
        raise ValueError('Native inventory rules version mismatch')
    return result


def with_native_inventory(base):
    """为当前环境添加显式兼容资产定义；保留部署身份及空地图门禁，不把资源目录标成战斗已验证。"""
    manifest = native_inventory_manifest()
    catalog = copy.deepcopy(base)
    catalog['native_assets'] = {'rules_version': NATIVE_INVENTORY_VERSION,
        'manifest_sha256': NATIVE_MANIFEST_SHA256, 'source_sha256': manifest['source_sha256'],
        'evidence': 'COMPATIBILITY_INVENTORY_ONLY', 'native_combat_verified': False}
    # None明确表示没有业务总槽限；网络包大小和单次操作数量仍有独立边界。
    catalog['inventory_limit'] = None
    from .native_wallet import wallet_catalog
    catalog['currencies'].update(wallet_catalog())
    for row in manifest['items']:
        from .native_features_catalog import item_definition, counter_item_ids
        features = item_definition(row['item_id'])
        # 当前四类抽奖及工坊配方材料使用StackNum计数，不更改旧实例ID或将枪械标签当材料。
        counter = row['item_id'] in counter_item_ids()
        catalog['items']['cn2020:'+str(row['item_id'])] = {
            'label': features['name'], 'stack': 2147483647 if counter else 1, 'permanent': True,
            # bHasUniqueness只保留在来源目录，尚未证明它表示账号独占；不改变旧版允许重复权益的合同。
            'duration_max': 315360000, 'exclusive': False, 'consumable': counter,
            'storage_mode': 'counter' if counter else 'instance',
            'slot': None, 'evidence': 'COMPATIBILITY_INVENTORY_ONLY',
            'native': dict(row), 'rules_version': NATIVE_INVENTORY_VERSION}
    return catalog


def native_equipment_available(item):
    """开放基础槽位与已有明确父子映射的核心；仍需业务层验证持有者及期限。

    隐藏状态表示UI展示策略，不能将所有隐藏组件一概开放；其余隐藏项仍需独立证据。
    此函数不替代业务层的账号、期限、GID和目标背包检查。
    """
    # boot73初霜的真实A008使用根1、自动槽；冻结目录将PvE角色归到64，不能仅开放两阵营10/11。
    # boot78背包100037使用同一种自动槽请求；两个隐藏默认背包已在登录资产树中核验。
    from .native_features_catalog import crafting_catalog, avatar_catalog
    from .native_pve_benefits import functional
    if functional(item):
        return True  # 仅已核实的七个限时/永久功能卡ID，根归属仍由装备事务核验。
    from .native_avatar_body import catalog as body_catalog
    if item.get('location') == -1 and str(item.get('item_id')) in body_catalog()['parts']:
        return True  # 原始默认身体资源仍须在业务层核对本人角色和家族，不能装备到背包。
    component=avatar_catalog()['components'].get(str(item.get('item_id')))
    if component and component['location']==item.get('location') and item.get('consume_type') in (0,1,2):
        return True
    if item.get('location')==94 and item.get('item_id') in crafting_catalog()['kernels']:
        return True
    return (item.get('location') in (0,1,2,3,10,11,12,64) and item.get('consume_type') in (0,1,2)
            and (not item.get('hidden') or item.get('item_id') in (100013,100026,100063)))


def pve_catalog(base):
    """把可读的模板展开为冻结规则；每张测试图都有独立规则 ID 和明确测试证据。"""
    profile = json.loads((Path(__file__).resolve().parents[1] / 'config/test-pve-profile.json').read_text(encoding='utf-8'))
    if base['id'] != profile['base_profile']:
        raise ValueError('PvE test profile base version mismatch')
    catalog = copy.deepcopy(base)
    catalog['id'] = profile['id']
    catalog['maps'] = {}
    for key, definition in profile['modes'].items():
        common = copy.deepcopy(profile['common_rules'])
        rules = dict(common, **copy.deepcopy(definition), id=profile['id'] + ':' + key, evidence='TEST_PROFILE')
        for stage in rules['stages']:
            stage['waves'] = [[copy.deepcopy(common[name]) for name in wave] for wave in stage['waves']]
        catalog['maps'][key] = {'capacity': definition['capacity'], 'min_players': definition['min_players'],
                                'difficulties': ['default'], 'channels': ['local'], 'pve_rules': rules, 'evidence': 'TEST_PROFILE'}
    return catalog
