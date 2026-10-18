"""固定当前客户端的操作目录；规则证据与原生界面、战斗验收分开保存。"""
import hashlib
import json
from functools import lru_cache
from pathlib import Path

FEATURE_VERSION = 'cn2020-feature-catalog-v1'
# 纯合同常量供桌面桥接使用，避免为读取版本而导入Django模型或打开业务数据库。
ROOM_VERSION = 'cn2020-native-room-compat-v2'
FEATURE_SHA256 = 'ffadf0914a77f712d71ba967367676217b72668f558dcbec14c76ece4fded538'
FRAGMENTS = {'epic':102111, 'selected':102432, 'fine':101517, 'rare':101144}


@lru_cache(maxsize=1)
def feature_catalog():
    """校验元数据摘要后返回固定快照，拒绝运行中静默替换操作和价格。"""
    raw = (Path(__file__).resolve().parents[1]/'config/cn2020-feature-catalog-v1.json').read_bytes()
    if hashlib.sha256(raw).hexdigest() != FEATURE_SHA256:
        raise ValueError('NATIVE_FEATURE_CATALOG_MISMATCH')
    return json.loads(raw)


def item_definition(item_id):
    """返回目录物品定义；未知ID返回None，由调用方形成业务拒绝。"""
    return feature_catalog()['items'].get(str(item_id))


def record_fields(value):
    """只展开固定提取产物的配置字段，保持原文件中的列表顺序与配方索引。"""
    return {row['key']:row['value'] for row in value['fields']}


@lru_cache(maxsize=1)
def counter_item_ids():
    """配置中ConsumeType0且StackNum为正的446类计数物品，加上明确工坊材料。

    正数StackNum是初始计数证据，不当作持有上限；兼容规则用有符号32位数量。
    只影响以后数量操作，历史多实例与GID不会合并或重写。
    """
    catalog=feature_catalog()
    configured=frozenset(row['item_id'] for row in catalog['items'].values()
        if row['consume_type']==0 and isinstance(row['stack_raw'],int) and row['stack_raw']>0)
    return configured | frozenset(FRAGMENTS.values()) | frozenset(record_fields(row)['FragmentPropId']
        for row in catalog['lottery']['BestFragmentDatas'])
@lru_cache(maxsize=1)
def crafting_catalog():
    """加载独立冻结的加工关系，不能因客户端工作文件变化而改变既有服务规则。"""
    raw=(Path(__file__).resolve().parents[1]/'config/cn2020-crafting-v1.json').read_bytes()
    if hashlib.sha256(raw).hexdigest()!='389f58cf1045c60f95c73b85a166a58987ed0410e2d85c1b0d84424c9179347e':
        raise ValueError('CRAFTING_CATALOG_MISMATCH')
    return json.loads(raw)


def attachment_compatible(item, parent):
    """当前UI明确的七个鲲核心只能挂在天鲲主体103428，隐藏标志不作为禁用理由。"""
    rules=crafting_catalog()
    return item.get('location')==94 and item.get('item_id') in rules['kernels'] and parent.get('item_id')==rules['kernel_parent']


@lru_cache(maxsize=1)
def avatar_catalog():
    """固定当前164个角色与158个装饰家族，禁止运行中替换父子兼容规则。"""
    raw=(Path(__file__).resolve().parents[1]/'config/cn2020-avatar-components-v1.json').read_bytes()
    if hashlib.sha256(raw).hexdigest()!='0c54707be7c8e59630d3a6aca30189d4f8dde787baf75ae1465861d86c00366c':
        raise ValueError('AVATAR_CATALOG_MISMATCH')
    return json.loads(raw)


def avatar_compatible(item, parent):
    """同家族装饰只能挂在对应角色实例；同号的武器槽绝不能作为角色挂件。"""
    rules=avatar_catalog()
    component=rules['components'].get(str(item.get('item_id')))
    role=rules['roles'].get(str(parent.get('item_id')))
    return bool(component and role and component['family']==role['family']
                and item.get('location')==component['location'] and parent.get('location')==role['location'])


@lru_cache(maxsize=1)
def exchange_catalog():
    """固定139条配方及界面协议号；不能根据客户端提交的奖励或材料字段计算产物。"""
    raw=(Path(__file__).resolve().parents[1]/'config/cn2020-exchange-v1.json').read_bytes()
    if hashlib.sha256(raw).hexdigest()!='e48c6530b22ff4efbe7c5467c49d5e74fe56186c690a004930bd1c684d99f902':
        raise ValueError('EXCHANGE_CATALOG_MISMATCH')
    return json.loads(raw)


@lru_cache(maxsize=1)
def engineering_catalog():
    """固定37条工程制造和268种可分解物品；保留隐藏定义，不从请求推导成本或产出。"""
    raw=(Path(__file__).resolve().parents[1]/'config/cn2020-engineering-v1.json').read_bytes()
    if hashlib.sha256(raw).hexdigest()!='e04bd1b45a3d8f8ccd2563f34dcd492be0b4018db4bb98f2630a05d0afc896a5':
        raise ValueError('ENGINEERING_CATALOG_MISMATCH')
    return json.loads(raw)
