"""只读固定当前TDR，导出功能消息及依赖布局；静态布局必须另经原生DLL往返验证。"""
import argparse
import hashlib
import json
import re
import struct
from pathlib import Path

EXPECTED = 'c7050a5d78edb68eb66b2b1e00e68640a762398b1d7c7f9b9072e4c74f915dba'
BASE, HEADER, FIELD, NONE = 0x114, 0xB8, 0xB4, 0xffffffff
TOKENS = ('BuyCommodity','PropRenew','PropAddAvaildPeriod','ChangeWeaponSkin','EquippedWeaponSkin','SwallowKunTaoPet',
          '_DailyAward','_IsAwardedDaily','Friend','Clan','LotteryExchange','FragmentFlow','FragmentPageOperate',
          'UpdatePlayerProperty','ChatP2P','ChatError','DelDepotProp','DropProp','PropDecompose','PropCompose','PropExchange',
          'CustomExchange','UseCard','KunTaoPet','SnsInfoList','Enemy','Group','Calendar','DailyNotice',
          'PlayerActvList','CheckActv','NtfButton','Approve','ConfirmJoin','TransferAdmin','InviteJoin','RefreshPlayer',
          'FortuneExchange','RewardCardExchange','MaterialDecompose','MaterialCompose','DailyLottery','PlayerSpecialityInfo',
          'VipInfo','TakeVipProps')


def extract(path):
    """交叉校验命名表、结构头及引用边界，返回声明结构；不推断未知字段业务含义。"""
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest()!=EXPECTED:
        raise ValueError('CURRENT_TDR_MISMATCH')
    def words(offset,count):
        """每次数组读取先检查实际文件边界。"""
        if offset<0 or offset+count*4>len(raw):
            raise ValueError('TDR_RANGE')
        return struct.unpack_from('<'+'I'*count,raw,offset)
    def name(pointer):
        """只读取被结构引用的有界ASCII名称，不导出任意字符串。"""
        offset=BASE+pointer
        if not BASE<=offset<len(raw):
            raise ValueError('TDR_SYMBOL_RANGE')
        end=raw.find(b'\0',offset,offset+256)
        if end<0:
            raise ValueError('TDR_SYMBOL_END')
        value=raw[offset:end].decode('ascii')
        if not re.fullmatch('[A-Za-z_][A-Za-z0-9_]*',value):
            raise ValueError('TDR_SYMBOL')
        return value
    header=words(0,36)
    if header[:3]!=(0xb02d6,32,len(raw)) or header[20]!=header[12]*16 or header[21]!=header[20]+header[10]*8:
        raise ValueError('TDR_HEADER')
    table={}
    for index in range(header[10]):
        pointer,relative=words(BASE+header[21]+index*8,2)
        h=words(BASE+relative,46)
        if h[33]!=pointer or h[15]!=relative or h[41]!=HEADER+h[11]*FIELD or h[5]<h[41] or h[11]>4096:
            raise ValueError('TDR_META_HEADER')
        table[name(pointer)]=(relative,h)
    def fields(meta):
        """导出x86最大主机布局及嵌套/计数引用，线上大小由独立编码器验证。"""
        relative,h=table[meta]
        result=[]
        for index in range(h[11]):
            v=words(BASE+relative+HEADER+index*FIELD,45)
            result.append({'name':name(v[3]),'type':v[2],'offset':v[11],
                'size':v[7],'capacity':v[9],'string_capacity':v[8] if v[2]==21 else None,
                'nested':None if v[30]==NONE else name(words(BASE+v[30],46)[33]),
                'reference':None if v[24]==NONE else name(words(BASE+v[24],45)[3]),
                'selector':None if v[0]==NONE else v[0]})
        return result
    commands={}
    for f in fields('C2ZNPkgBody'):
        if f['nested'] and any(token.lower() in f['nested'].lower() for token in TOKENS):
            commands[str(f['selector'])]=f['nested']
    pending=list(commands.values())+['PlayerInfo','PropInfo']
    schemas={}
    while pending:
        meta=pending.pop()
        if meta in schemas:
            continue
        rows=fields(meta)
        schemas[meta]={'host_size':table[meta][1][7],'fields':rows}
        pending.extend(f['nested'] for f in rows if f['nested'])
    if path.read_bytes()!=raw:
        raise ValueError('TDR_CHANGED')
    constants = {}
    for index in range(header[12]):
        pointer,value,_,_ = words(BASE+index*16,4)
        label = name(pointer)
        if not label.startswith('ID_') and (label.startswith(('ZONE_','SNS_','SHOP_','CLAN_','LOTTERY_','EXCHANGE_','KUNTAO_','WEAPON_SKIN_','EUpdate','EProp','KunTao','ECHATTYPE_',
                'PROPFACTORY_','PROPFACTROY_','EPROPDECOMPOSE_','EPROPCOMPOSETYPE_','SPECIALITYSTAGE_'))):
            constants[label] = value
    return {'metadata_sha256':EXPECTED,'commands':commands,'schemas':schemas,'constants':constants,
            'evidence':'STATIC_LAYOUT_REQUIRES_DLL_AND_LIVE_VALIDATION'}


def main():
    """新文件导出指定元数据，拒绝覆盖历史证据。"""
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    result=extract(args.source)
    raw=(json.dumps(result,sort_keys=True,separators=(',',':'))+'\n').encode()
    with args.output.open('xb') as stream:
        stream.write(raw)
    print(json.dumps({'commands':len(result['commands']),'schemas':len(result['schemas']),'sha256':hashlib.sha256(raw).hexdigest()}))


if __name__=='__main__':
    main()
