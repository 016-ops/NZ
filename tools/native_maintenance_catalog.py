"""从固定客户端目录冻结维修/出售/续费字段；原始文件只读，派生规则单独版本化。"""
import hashlib,json
from pathlib import Path
from tools.client_catalog import decode_resource,parse_ini,fields
from tools.native_compat_catalog import SOURCE_SHA256

VERSION='cn2020-item-maintenance-v1'


def extract(workspace):
    """保持4323项覆盖，保存原始维修类型、金额和商品关联，不将商品ID当价格。"""
    path=Path(workspace)/'TGame/Config/DefaultItemLibrary.ini'
    raw=path.read_bytes()
    if hashlib.sha256(raw).hexdigest()!=SOURCE_SHA256:
        raise ValueError('CURRENT_ITEM_LIBRARY_MISMATCH')
    content,_=decode_resource(raw);items={}
    for row in parse_ini(content,path.name):
        if row.get('key')!='ItemDatas':continue
        value=fields(row['value']);key=str(value['nItemID'])
        if key in items:raise ValueError('DUPLICATE_ITEM')
        items[key]={k:value.get(k,False if k.startswith('b') else 0) for k in
            ('ConsumeType','RepairType','DurableMax','nRepairPrice','nRepairPrice1','bDroppable','bDefaultItem')}
    if len(items)!=4323:raise ValueError('CURRENT_ITEM_COUNT_MISMATCH')
    return dict(version=VERSION,source_sha256=SOURCE_SHA256,items=items,
        pricing='CLIENT_SCRIPT_REPAIR_CEIL_MISSING_RATIO_AND_SELL_QUARTER_REMAINING_RATIO')


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();result=extract(args.workspace)
    raw=(json.dumps(result,sort_keys=True,separators=(',',':'))+'\n').encode()
    with args.output.open('xb') as stream:stream.write(raw)
    print(json.dumps(dict(items=len(result['items']),sha256=hashlib.sha256(raw).hexdigest())))
