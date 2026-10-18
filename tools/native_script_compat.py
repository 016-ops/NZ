"""固定当前UI脚本的两处日期赋值修复；仅生成受守卫恢复的工作副本，不修改原客户端。"""
import hashlib
import io
import struct
from tools.native_package_dependencies import decompress_chunk, Cipher, algorithms, modes, SCRIPT_RESOURCE_KEY

PROFILE='cn2020-workshop-date-fields-v1'
SOURCE_SHA256='cd213dcd3157bb879dcd10a7aecf490151a87bbaa6c711cbdfc3f8d04fafea6f'
FUNCTION_SHA256='6a92ed638e89bb676fa4a7cf53dbc632a75a126e517e2748167a37aeae36443e'
RELATIVE='TGame/CookedPC/TGameGfxUI.u'


def fix_workshop_date(original):
    """按完整包和函数摘要锁定版本，修正LastMon/LastDay目标，保留全部未改解压字节与导出偏移。"""
    if hashlib.sha256(original).hexdigest()!=SOURCE_SHA256:
        raise ValueError('CURRENT_SCRIPT_FINGERPRINT_MISMATCH')
    import lzokay
    tag,end=struct.unpack_from('<II',original)
    assert tag==0x3e27a900
    count=(end-8)//16*16
    decoder=Cipher(algorithms.AES(SCRIPT_RESOURCE_KEY),modes.ECB()).decryptor()
    header=bytearray(decoder.update(original[8:8+count])+decoder.finalize())
    delta=struct.unpack_from('<i',header,8)[0]-5
    chunks=struct.unpack_from('<I',header,109+delta)[0]
    result=bytearray(original[:end]);last=end;changed=0
    for index in range(chunks):
        descriptor=113+delta+16*index
        logical,size,physical=struct.unpack_from('<III',header,descriptor)
        magic,block_size,compressed,total=struct.unpack_from('<IIII',original,physical)
        stored=16+8*((total+block_size-1)//block_size)+compressed
        if magic!=0x9e2a83c1 or size!=total or physical!=last:
            raise ValueError('CURRENT_SCRIPT_CHUNK_LAYOUT_MISMATCH')
        chunk=original[physical:physical+stored]
        # 当前导出OnNtfDailyLotteryInfo恰有217字节，替换两处对象索引不会改变VM跳转偏移。
        if logical<=6683081 and 6683298<=logical+size:
            clear=bytearray(decompress_chunk(io.BytesIO(original),physical,stored,size,len(original)))
            at=6683081-logical
            if hashlib.sha256(clear[at:at+217]).hexdigest()!=FUNCTION_SHA256:
                raise ValueError('CURRENT_SCRIPT_FUNCTION_MISMATCH')
            for offset,parameter,target in ((59,0x4a93,0xd92d),(70,0x4a92,0xd92c)):
                anchor=b'\x0f\x01'+struct.pack('<i',0xd92e)+b'\x00'+struct.pack('<i',parameter)
                assert clear[at+offset:at+offset+11]==anchor
                struct.pack_into('<i',clear,at+offset+2,target)
                changed+=1
            pieces=[lzokay.compress(bytes(clear[i:i+block_size])) for i in range(0,size,block_size)]
            lengths=[min(block_size,size-i) for i in range(0,size,block_size)]
            chunk=struct.pack('<IIII',magic,block_size,sum(map(len,pieces)),size)
            chunk+=b''.join(struct.pack('<II',len(p),n) for p,n in zip(pieces,lengths))+b''.join(pieces)
            assert decompress_chunk(io.BytesIO(chunk),0,len(chunk),size,len(chunk))==clear
        # 最后描述符只有部分大小字节处于AES块内，未覆盖高位必须与原值相同；不猜测加密尾部。
        visible=min(4,len(header)-descriptor-12)
        before=struct.pack('<I',stored);after=struct.pack('<I',len(chunk))
        if header[descriptor+12:descriptor+12+visible]!=before[:visible] or before[visible:]!=after[visible:]:
            raise ValueError('CURRENT_SCRIPT_PARTIAL_DESCRIPTOR_MISMATCH')
        struct.pack_into('<I',header,descriptor+8,len(result))
        header[descriptor+12:descriptor+12+visible]=after[:visible]
        result.extend(chunk);last=physical+stored
    # 当前固定包有432字节UXIT尾记录，逐字节保留；不能误当作压缩数据或自行重建。
    if len(original)-last!=432 or original[last:last+4]!=b'UXIT' or changed!=2:
        raise ValueError('CURRENT_SCRIPT_PATCH_COUNT_MISMATCH')
    result.extend(original[last:])
    encoder=Cipher(algorithms.AES(SCRIPT_RESOURCE_KEY),modes.ECB()).encryptor()
    result[8:8+count]=encoder.update(bytes(header))+encoder.finalize()
    return bytes(result)


def install(guard):
    """交给同一恢复守卫保存原件、安装以及退出恢复；摘要不符时停止启动，不覆盖未知版本。"""
    original=(guard.root/RELATIVE).read_bytes()
    variant=fix_workshop_date(original)
    record=guard.apply(RELATIVE,variant)
    return dict(profile=PROFILE,original_sha256=SOURCE_SHA256,variant_sha256=record['variant_sha256'],assignments_fixed=2)
