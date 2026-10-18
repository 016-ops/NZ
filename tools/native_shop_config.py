"""在Launcher受控工作副本恢复续费商品入口；完整摘要锁定，退出由同一守卫还原。"""
import hashlib
import re
import struct
import zlib

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from tools.client_catalog import MAGIC, RESOURCE_KEY, decode_resource
from revival.native_shop_compat import restored_offers

RELATIVE='TGame/Config/DefaultCommodityLibrary.ini'


def restore_renewal_offers(original):
    """仅改变固定商品行的两个布尔开关；保留中文原始字节、价格、日期和所有其他配置。

    原生GetCurrentRepairPriceValue要求bShowCommodity与bGoodsAdded同时为真。
    同一兼容规则在服务端允许这些商品正常报价，避免新增界面入口再次遭遇假下架。
    """
    rules=restored_offers()
    if hashlib.sha256(original).hexdigest()!=rules['source_sha256']:
        raise ValueError('CURRENT_SHOP_CONFIG_FINGERPRINT_MISMATCH')
    decode_resource(original)
    decoder=Cipher(algorithms.AES(RESOURCE_KEY),modes.ECB()).decryptor()
    packed=decoder.update(original[len(MAGIC):])+decoder.finalize()
    clear=zlib.decompress(packed[4:])
    targets=set(rules['offer_ids']);seen=set();lines=[];section=None
    for line in clear.splitlines(keepends=True):
        header=re.fullmatch(rb'\[([^\]]+)\]\r?\n?',line.removeprefix(b'\xef\xbb\xbf'))
        if header:section=header[1]
        match=re.match(rb'RawCommodityDatas=\(nCommodityID=(\d+),',line)
        if section==b'UTGame.TGFPSCommodityLibrary' and match and int(match[1]) in targets:
            key=int(match[1])
            if key in seen:raise ValueError('DUPLICATE_RENEWAL_OFFER')
            seen.add(key)
            for field in (b'bShowCommodity',b'bGoodsAdded'):
                # 使用字段分隔符，不能意外改到字符串描述或近似属性名。
                pattern=rb'(?<=, )'+field+rb'=(?:false|true)(?=,)'
                line,count=re.subn(pattern,field+b'=true',line)
                if count!=1:raise ValueError('RENEWAL_OFFER_FIELD_MISMATCH')
        lines.append(line)
    if seen!=targets:raise ValueError('RENEWAL_OFFER_SET_MISMATCH')
    rewritten=b''.join(lines)
    packed=struct.pack('<I',len(rewritten))+zlib.compress(rewritten)
    packed+=bytes((-len(packed))%16)
    encoder=Cipher(algorithms.AES(RESOURCE_KEY),modes.ECB()).encryptor()
    result=MAGIC+encoder.update(packed)+encoder.finalize()
    _,metadata=decode_resource(result)
    if metadata['decoded_sha256']!=hashlib.sha256(rewritten).hexdigest():
        raise ValueError('RENEWAL_CONFIG_ROUNDTRIP_MISMATCH')
    return result


def install(guard):
    """原配置先备份，再原子安装；返回可审计规则版本和摘要，不修改原始客户端。"""
    rules=restored_offers()
    variant=restore_renewal_offers((guard.root/RELATIVE).read_bytes())
    record=guard.apply(RELATIVE,variant)
    return dict(profile=rules['version'],offers=len(rules['offer_ids']),
                original_sha256=rules['source_sha256'],variant_sha256=record['variant_sha256'])
