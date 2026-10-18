"""为指定房间节点生成固定源默认地图配置；资源池只读共享，不使用硬链接或目录联接。"""
import hashlib
import json
from pathlib import Path
import re,struct,zlib
from cryptography.hazmat.primitives.ciphers import Cipher,algorithms,modes
from .client_catalog import MAGIC,RESOURCE_KEY,decode_resource

SOURCE_SHA256='575574e060fad4d529eda1ee53e00e67db77746144d070bd9fa8cc3dc3bded1b'
RULES_SHA256='b45d3da2901eedc8be0574b7e523bdf088eea96ae0360848f4e09f52f05c4135'
SHARED='C:/NZResearch/client-workspaces/cn2020-room-20261018/TGame/CookedPC'
PROFILE='cn2020-native-room-engine-v2-single-resource-root'


def variant(original,*,map_id=168,port=17777):
    """生成单一共享资源搜索根，只允许目录内主图及非特权端口；不伪造难度或节点就绪。

    UE3按包名索引CookedPC。独立入口已带75份原脚本/地图，若在本地根之外追加同一共享池，
    会在初始化阶段触发Ambiguous package name并等待确认。共享池已含这些原件，故直接替换
    SeekFreePCPaths；私有副本仍保留原文件供守卫校验和恢复，不删除或覆盖任何资源。
    """
    if not isinstance(original,bytes) or hashlib.sha256(original).hexdigest()!=SOURCE_SHA256:
        raise ValueError('ROOM_ENGINE_SOURCE_CHANGED')
    if type(port) is not int or not 1024<=port<=32767 or type(map_id) is not int:raise ValueError('ROOM_SERVER_ARGUMENTS')
    raw=(Path(__file__).resolve().parents[1]/'config/cn2020-native-room-compat-v1.json').read_bytes()
    if hashlib.sha256(raw).hexdigest()!=RULES_SHA256:raise ValueError('ROOM_SERVER_RULES_CHANGED')
    rule=json.loads(raw)['maps'].get(str(map_id))
    if not rule or not rule['main_resource']:raise ValueError('ROOM_SERVER_MAIN_MAP_MISSING')
    _,meta=decode_resource(original,limit=1024*1024)
    dec=Cipher(algorithms.AES(RESOURCE_KEY),modes.ECB()).decryptor()
    packed=dec.update(original[len(MAGIC):])+dec.finalize();clear=zlib.decompress(packed[4:])
    decoded=clear.decode(meta['encoding'])
    if decoded.encode(meta['encoding'])!=clear:raise ValueError('ROOM_ENGINE_ENCODING_CHANGED')
    section=None;changed=[];lines=[]
    for line in decoded.splitlines(keepends=True):
        header=re.fullmatch(r'\[([^\]]+)\]\r?\n?',line)
        if header:section=header[1]
        match=re.fullmatch(r'(Map|LocalMap)=UTFrontEnd\.udk(\r?\n)?',line)
        if section=='URL' and match:
            changed.append(match[1]);line=match[1]+'='+rule['map_name']+'.udk?listen'+(match[2] or '')
            if match[1]=='LocalMap':line+='Port='+str(port)+(match[2] or '\r\n')
        if section=='Core.System' and line.rstrip('\r\n')=='SeekFreePCPaths=..\\..\\TGame\\CookedPC':
            changed.append('SharedResourcePath');line='SeekFreePCPaths='+SHARED+'\r\n'
        lines.append(line)
    # 原件-Suppress=Warning/Init已经移除继承的抑制项，必须原样保留减号语义。
    if sorted(changed)!=sorted(['Map','LocalMap','SharedResourcePath']):
        raise ValueError('ROOM_ENGINE_LAYOUT_CHANGED')
    rewritten=''.join(lines).encode(meta['encoding'])
    packed=struct.pack('<I',len(rewritten))+zlib.compress(rewritten);packed+=bytes((-len(packed))%16)
    enc=Cipher(algorithms.AES(RESOURCE_KEY),modes.ECB()).encryptor();result=MAGIC+enc.update(packed)+enc.finalize()
    text,_=decode_resource(result,limit=1024*1024)
    if text!=rewritten.decode(meta['encoding']).rstrip('\0'):raise ValueError('ROOM_ENGINE_ROUNDTRIP_FAILED')
    return result
