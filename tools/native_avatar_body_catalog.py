"""从当前固定物品表提取默认身体部件；隐藏手臂资源必须跟随角色，不作为背包武器槽。"""
import argparse
import hashlib
import json
from pathlib import Path

from tools.client_catalog import decode_resource, fields, parse_ini
from tools.native_compat_catalog import SOURCE_SHA256

VERSION = 'cn2020-avatar-body-compat-v1'


def extract(path):
    """保留原始资源号和 Location=-1，只接受已知手臂/身体资源类型及唯一家族默认部件。"""
    raw = Path(path).read_bytes()
    if hashlib.sha256(raw).hexdigest() != SOURCE_SHA256:
        raise ValueError('CURRENT_ITEM_LIBRARY_MISMATCH')
    content, _ = decode_resource(raw)
    parts = {}
    unique = set()
    for record in parse_ini(content, 'DefaultItemLibrary.ini'):
        if record.get('key') != 'ItemDatas':
            continue
        row = fields(record['value'])
        high, low = row['ResID1'], row['ResID2']
        resource_type = (high >> 20) & 255
        if high >> 28 != 0 or resource_type not in (1, 2) or not row.get('bDefaultItem'):
            continue
        if row.get('Location') != -1 or not row.get('bHidden'):
            raise ValueError('BODY_RESOURCE_LAYOUT_CHANGED')
        family = low >> 16
        if (family, resource_type) in unique:
            raise ValueError('AMBIGUOUS_DEFAULT_BODY_PART')
        unique.add((family, resource_type))
        parts[str(row['nItemID'])] = dict(item_id=row['nItemID'], family=family,
            resource_type=resource_type, location=-1, resource_high=high, resource_low=low)
    if not parts or not {'300062', '300063', '300067', '300068'} <= parts.keys():
        raise ValueError('STARTER_BODY_RESOURCES_MISSING')
    return dict(version=VERSION, source_sha256=SOURCE_SHA256, parts=parts,
        policy='Persist owned role children in distinct internal body slots; native wire retains original Location=-1.')


def main():
    """新增版本化配置，拒绝覆盖已有规则；不改客户端或数据库。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = extract(args.source)
    raw = (json.dumps(result, sort_keys=True, separators=(',', ':'))+'\n').encode()
    with args.output.open('xb') as stream:
        stream.write(raw)
    print(json.dumps(dict(parts=len(result['parts']), sha256=hashlib.sha256(raw).hexdigest())))


if __name__ == '__main__':
    main()
