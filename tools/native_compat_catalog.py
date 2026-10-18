"""从固定当前物品配置生成兼容库存目录；只导出结构元数据，不修改客户端或推断战斗效果。"""
import argparse
import hashlib
import json
from pathlib import Path

from tools.client_catalog import decode_resource, parse_ini, fields

SOURCE_SHA256 = '741963c2174004735a9456ea559a6976f53665f591f9ba995d7889b8db0911a6'
RULES_VERSION = 'cn2020-full-inventory-compat-v1'


def extract(path):
    """固定指纹后提取全部唯一ID；缺失ConsumeType的组件保留，但明确禁止未经验证的原生投影。"""
    raw = Path(path).read_bytes()
    if hashlib.sha256(raw).hexdigest() != SOURCE_SHA256:
        raise ValueError('CURRENT_ITEM_LIBRARY_MISMATCH')
    content, _ = decode_resource(raw)
    result, seen = [], set()
    for row in parse_ini(content, 'TGame/Config/DefaultItemLibrary.ini'):
        item = fields(row.get('value'))
        if 'nItemID' not in item:
            continue
        item_id, location = item['nItemID'], item.get('Location')
        if type(item_id) is not int or not 0 < item_id < 2**31 or item_id in seen:
            raise ValueError('INVALID_OR_DUPLICATE_NATIVE_ITEM_ID')
        if type(location) is not int or not -128 <= location <= 127:
            raise ValueError('INVALID_NATIVE_ITEM_LOCATION')
        consume = item.get('ConsumeType')
        if consume is not None and consume not in (0, 1, 2, 3):
            raise ValueError('UNKNOWN_NATIVE_CONSUME_TYPE')
        seen.add(item_id)
        result.append({'item_id': item_id, 'location': location, 'consume_type': consume,
            'durability_max': max(0, int(item.get('DurableMax', 0))),
            'hidden': bool(item.get('bHidden', False)),
            'projection_status': 'COMPATIBILITY_PENDING_LIVE' if consume is not None else 'COMPONENT_OWNER_UNVERIFIED'})
    if len(result) != 4323:
        raise ValueError('CURRENT_ITEM_COUNT_MISMATCH')
    return {'rules_version': RULES_VERSION, 'source_sha256': SOURCE_SHA256,
            'policy': 'One permanent bound inventory entitlement per configured item; native usability is separately verified.',
            'items': sorted(result, key=lambda row: row['item_id'])}


def main():
    """以新文件输出可审查的目录；存在文件时拒绝覆盖，避免悄悄改写既有规则版本。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = extract(args.source)
    with args.output.open('x', encoding='utf-8') as stream:
        json.dump(result, stream, ensure_ascii=True, separators=(',', ':'))
        stream.write('\n')
    print(json.dumps({'items': len(result['items']), 'rules_version': RULES_VERSION,
                      'sha256': hashlib.sha256(args.output.read_bytes()).hexdigest()}))


if __name__ == '__main__':
    main()
