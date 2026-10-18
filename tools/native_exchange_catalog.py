"""冻结当前升级配置与界面协议编号的交叉关系，保留139条配方和原始来源摘要。"""
import hashlib
import json
from pathlib import Path
from tools.client_catalog import decode_resource, parse_ini, fields
from revival.native_features_catalog import crafting_catalog


def extract(root):
    """每条配方必须唯一匹配主体、材料与协议号；缺失或歧义直接失败，不能按列表下标猜测。"""
    path = root/'TGame/Config/DefaultCommonDataLibrary.ini'
    raw = path.read_bytes()
    source, _ = decode_resource(raw)
    mappings = [fields(row['value']) for row in parse_ini(source,path.name)
                if row.get('key') == 'ExchangeItemInfoList']
    recipes = []
    for row in crafting_catalog()['upgrades']:
        candidates = [m for m in mappings if m['UpgradeItemId']==row['ExchangeItemId']
                      and m['CostItemId']==row['CostItemId']]
        if len(candidates)!=1 or candidates[0]['ExchangeMethodType']!='ExchangeType_UpgradeItem':
            raise ValueError('EXCHANGE_MAPPING_AMBIGUOUS')
        recipes.append(dict(row, protocol_value=candidates[0]['ProtocolValue'],
                            recipe_id=f"{row['ExchangeItemId']}:{candidates[0]['ProtocolValue']}"))
    if len(recipes)!=139 or len({r['recipe_id'] for r in recipes})!=139 or path.read_bytes()!=raw:
        raise ValueError('EXCHANGE_CATALOG_CHANGED')
    return {'version':'cn2020-exchange-v1','recipes':recipes,
            'sources':dict(crafting_catalog()['sources'], **{path.name:hashlib.sha256(raw).hexdigest()}),
            'evidence':'CURRENT_CONFIG_CROSS_REFERENCE_REQUIRES_NATIVE_VALIDATION'}


if __name__=='__main__':
    # 每个冻结版本只能创建一次，后续规则变更新增版本而不改写旧回执所依赖的定义。
    root=Path(__file__).resolve().parents[1]
    result=extract(root/'runtime/native-recovery/coherent-2020/nz')
    raw=(json.dumps(result,ensure_ascii=False,sort_keys=True,separators=(',',':'))+'\n').encode('utf-8')
    with (root/'config/cn2020-exchange-v1.json').open('xb') as stream:
        stream.write(raw)
    print(json.dumps({'recipes':len(result['recipes']),'sha256':hashlib.sha256(raw).hexdigest()}))
