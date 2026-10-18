"""只读提取当前工程分解和制造目录；保留隐藏/锁定项，缺失收益规则不冒充客户端已有配置。"""
import argparse
import hashlib
import json
from pathlib import Path
from tools.client_catalog import decode_resource, parse_ini, fields
from tools.native_compat_catalog import SOURCE_SHA256

VERSION = 'cn2020-engineering-catalog-v1'


def extract(root):
    """核验物品目录身份，导出部件、分解映射和37项制造定义，不读取玩家资产。"""
    sources = {}

    def records(filename):
        """每个来源读取前后摘要一致；只导出业务字段，原始脚本不进入产物。"""
        path = root/'TGame/Config'/filename
        raw = path.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        if filename == 'DefaultItemLibrary.ini' and digest != SOURCE_SHA256:
            raise ValueError('CURRENT_ITEM_LIBRARY_MISMATCH')
        parsed = parse_ini(decode_resource(raw)[0], filename)
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError('ENGINEERING_SOURCE_CHANGED')
        sources[filename] = digest
        return parsed

    # 类型数值来自当前TDR EPROPDECOMPOSE常量；不是按配置行顺序猜测。
    types = {'Project_DecomPose_Prop_GP': 1, 'Project_DecomPose_Prop_NZ': 2,
             'Project_DecomPose_Prop_MP': 3}
    parts, mappings, recipes, items = {}, {}, [], []
    for record in records('DefaultCommonDataLibrary.ini'):
        value = fields(record.get('value'))
        key = record.get('key')
        if key == 'Project_DecomposePartMappings':
            parts[value['nPartType']] = value['nItemID']
        elif key == 'Project_DecomposePropMappings':
            mappings[str(types[value['nPropType']])] = value['PartTypeArray']
        elif key == 'Project_SyntheticPropMappings':
            recipes.append({key: value[key] for key in (
                'nSyntheticPropID','nItemID','bLocked','bVisible','DirectMakeSuccRate',
                'MakeWithNZSuccRate','PriceValueSucceed_NZ','ProficientDegree_Max',
                'NormalAvaibleTimeArray','HighAvaibleTimeArray','NeedPartArray')})
    for record in records('DefaultItemLibrary.ini'):
        if record.get('key') != 'ItemDatas':
            continue
        value = fields(record['value'])
        if value.get('nDecomPoseType') in (1,2,3):
            items.append({'item_id':value['nItemID'],'type':value['nDecomPoseType']})
    for row in recipes:
        row['materials'] = [{'item_id':parts[value['nPartType']], 'quantity':value['nCount']}
                            for value in map(fields, row.pop('NeedPartArray'))]
    if len(parts)!=4 or len(recipes)!=37 or len(items)!=268:
        raise ValueError('CURRENT_ENGINEERING_COVERAGE_MISMATCH')
    return dict(version=VERSION, sources=sources, parts=parts,
                decompose_outputs={key:[parts[name] for name in names] for key,names in mappings.items()},
                decomposable=items, recipes=recipes,
                evidence='CURRENT_CONFIG_ONLY_YIELD_RANDOM_POOL_DURATION_UNITS_AND_COOLDOWN_REQUIRE_RULES')


def main():
    """只创建新目录文件；不覆盖已有版本，不自动启用或发布任何缺失规则。"""
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    result=extract(args.workspace.resolve(strict=True))
    raw=(json.dumps(result,sort_keys=True,ensure_ascii=False,separators=(',',':'))+'\n').encode('utf-8')
    with args.output.open('xb') as stream:
        stream.write(raw)
    print(json.dumps(dict(items=len(result['decomposable']),recipes=len(result['recipes']),sha256=hashlib.sha256(raw).hexdigest())))


if __name__=='__main__':
    main()
