"""提取当前升级配方、隐藏武器核心和宠物界面关系，保存为独立冻结版本。"""
import hashlib
import json
from pathlib import Path
from tools.client_catalog import decode_resource, parse_ini, fields


def extract(root):
    """只读受控副本；同时保存来源摘要，未知语义不改写成已实机验证。"""
    sources={};result={'version':'cn2020-crafting-v1','sources':sources,'upgrades':[],
                       'kernels':[],'swallow_ui':[],'evidence':'CURRENT_CONFIG_NOT_LIVE_ACCEPTANCE'}
    for name in ('DefaultUI.ini','DefaultUpgradeItemManager.ini'):
        raw=(root/'TGame/Config'/name).read_bytes()
        sources[name]=hashlib.sha256(raw).hexdigest()
        content,_=decode_resource(raw)
        for row in parse_ini(content,name):
            value=fields(row.get('value'))
            if row.get('key')=='UpgradeItemTable':
                result['upgrades'].append(value)
            elif row.get('key')=='KunWeaponItemID':
                result['kernel_parent']=row['value']
            elif row.get('key')=='KunWeaponKernelMaps':
                result['kernels'].append(value['KernelItemID'])
            elif row.get('section')=='TGameGfxUI.TGUI_KunMovie_Pet' and row.get('key')=='SwallowPetMaps':
                result['swallow_ui'].append(value)
    if len(result['upgrades'])!=139 or len(result['kernels'])!=7 or result.get('kernel_parent')!=103428:
        raise ValueError('CURRENT_CRAFTING_CATALOG_CHANGED')
    return result


if __name__=='__main__':
    # 固定工作副本和新增产物，拒绝覆盖已被后端引用的版本。
    root=Path(__file__).resolve().parents[1]
    data=extract(root/'runtime/native-recovery/coherent-2020/nz')
    raw=(json.dumps(data,ensure_ascii=False,sort_keys=True,separators=(',',':'))+'\n').encode('utf-8')
    with (root/'config/cn2020-crafting-v1.json').open('xb') as stream:
        stream.write(raw)
    print(json.dumps({'sha256':hashlib.sha256(raw).hexdigest(),'upgrades':len(data['upgrades'])}))
