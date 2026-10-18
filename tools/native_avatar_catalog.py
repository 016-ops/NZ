"""从固定当前物品表提取角色与装饰家族关系；不修改客户端，不把外观关系当作战斗验收。"""
import argparse
import hashlib
import json
from pathlib import Path
from tools.client_catalog import decode_resource, parse_ini, fields
from tools.native_compat_catalog import SOURCE_SHA256

VERSION = 'cn2020-avatar-components-v1'


def extract(path):
    """冻结原始ResID和位置，按当前配置的角色/装饰家族编码生成可审查的兼容关系。"""
    raw = Path(path).read_bytes()
    if hashlib.sha256(raw).hexdigest() != SOURCE_SHA256:
        raise ValueError('CURRENT_ITEM_LIBRARY_MISMATCH')
    content, _ = decode_resource(raw)
    roles, components = {}, {}
    for record in parse_ini(content, 'DefaultItemLibrary.ini'):
        if record.get('key') != 'ItemDatas':
            continue
        row = fields(record['value'])
        high, low, location = row['ResID1'], row['ResID2'], row['Location']
        common = dict(item_id=row['nItemID'], location=location, resource_high=high, resource_low=low)
        # 角色高32位的家族字段对应装饰低32位的家族字段；不按商品顺序或中文名称猜父级。
        # 当前158项装饰仅有0/1/2三个独立槽，和背包同号槽必须用父级类型区分。
        if high >> 28 == 7 and location in (10, 11, 64):
            roles[str(row['nItemID'])] = dict(common, family=(high >> 20) & 255)
        elif high >> 28 == 1:
            if location not in (0, 1, 2):
                raise ValueError('AVATAR_COMPONENT_SLOT_CHANGED')
            components[str(row['nItemID'])] = dict(common, family=low >> 16, default=bool(row.get('bDefaultItem')))
    if len(components) != 158 or not roles:
        raise ValueError('AVATAR_CATALOG_COUNT_CHANGED')
    defaults = set()
    for component in components.values():
        if component['default']:
            key = (component['family'], component['location'])
            if key in defaults:
                raise ValueError('AMBIGUOUS_DEFAULT_COMPONENT')
            defaults.add(key)
    return dict(version=VERSION, source_sha256=SOURCE_SHA256,
        policy='Current ResID family equality; preserve instances and occupied cosmetic slots; native rendering verified separately.',
        roles=roles, components=components)


def main():
    """仅创建新的规则文件；摘要用于服务端校验，拒绝覆盖既有版本。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    data = extract(args.source)
    raw = (json.dumps(data, sort_keys=True, separators=(',', ':')) + '\n').encode()
    with args.output.open('xb') as stream:
        stream.write(raw)
    print(json.dumps(dict(roles=len(data['roles']), components=len(data['components']), sha256=hashlib.sha256(raw).hexdigest())))


if __name__ == '__main__':
    main()
