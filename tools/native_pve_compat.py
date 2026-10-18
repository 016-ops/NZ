"""固定当前构建的单机PvE结束返厅兼容层；只生成工作副本变体，不提供通用脚本修改入口。

原生PVEGame.EndGame完成胜负状态后缺少Standalone返厅定时器，塔防/保卫会停留在黑屏。
本层保留原结束函数全部逻辑，仅在其正常返回前，按原生TGSVGame的相同条件调用既有
ConsoleCommand_Disconnect；在线房间不进入该分支。真实加载与返厅必须另行实机验证。
"""
import hashlib
import io
import struct

from tools.native_package_dependencies import Cipher, algorithms, modes, SCRIPT_RESOURCE_KEY, decompress_chunk

PROFILE = 'cn2020-standalone-pve-return-v2-candidate'
RELATIVE = 'TGame/CookedPC/PVEGame.u'
SOURCE_SHA256 = 'f01e97250aa6ff000a98b58cfd1dd0d8ce87bb0b719f33f08c7f9b0ddfde921c'
FUNCTION_SHA256 = 'eb8fe1b335e41ff14371106956419cdbf76c5496339c9d07b8367b63e159ed76'
# 相同包TGSVGame.EndGameReason中已核验的NetMode==NM_Standalone表达式及虚函数调用。
# 表达式含3个UObject/UProperty引用，序列化各4字节、VM偏移各8字节；FName不扩展。
STANDALONE_CONDITION = bytes.fromhex('9a383a1901e9ecffff09000dfdffff00010dfdffff383a240016')
RETURN_CALL = bytes.fromhex('1b071700000000000016')


class _Package:
    """只解析当前PVE包的固定摘要、压缩块和导出元数据；所有读取都有长度上界。"""

    def __init__(self, raw):
        """解析构建已固定的包；原件摘要由公开入口验证，回读变体复用同样结构检查。"""
        self.raw = raw
        if not isinstance(raw, bytes) or not 1024 <= len(raw) <= 20*1024*1024:
            raise ValueError('PVE_PACKAGE_SIZE_INVALID')
        tag, self.end = struct.unpack_from('<II', raw)
        if tag != 0x3e27a900 or not 128 <= self.end < 65536:
            raise ValueError('PVE_PACKAGE_HEADER_INVALID')
        # 当前封装在尾部保存完整800字节加密目录，头部只保存其前789字节。
        # 必须读取完整AES块并保持两份目录一致；不能只更新可见的头部784字节。
        self.cipher_size = 800
        if self.end != 797 or raw[8:self.end] != raw[-self.cipher_size:][:self.end-8]:
            raise ValueError('PVE_ENCRYPTED_DIRECTORY_MIRROR_MISMATCH')
        decoder = Cipher(algorithms.AES(SCRIPT_RESOURCE_KEY), modes.ECB()).decryptor()
        self.header = bytearray(decoder.update(raw[-self.cipher_size:])+decoder.finalize())
        self.delta = struct.unpack_from('<i', self.header, 8)[0]-5
        if struct.unpack_from('<I', self.header)[0] != 0x202fc or not 0 <= self.delta <= 1024:
            raise ValueError('PVE_PACKAGE_SUMMARY_INVALID')
        nc, no, ec, eo, ic, ioff = struct.unpack_from('<IIIIII', self.header, 21+self.delta)
        if nc != 32026 or ec != 87912 or struct.unpack_from('<I',self.header,105+self.delta)[0] != 2:
            raise ValueError('PVE_PACKAGE_TABLE_LAYOUT_INVALID')
        self.export_offset, self.export_count = eo, ec
        count = struct.unpack_from('<I', self.header, 109+self.delta)[0]
        if not 1 <= count <= 1024:
            raise ValueError('PVE_PACKAGE_CHUNK_COUNT_INVALID')
        self.chunks, self.cache = [], {}
        physical_end, logical_end = self.end, None
        for i in range(count):
            descriptor = 113+self.delta+16*i
            logical, size, physical = struct.unpack_from('<III', self.header, descriptor)
            magic, block, compressed, total = struct.unpack_from('<IIII', raw, physical)
            stored = 16+8*((total+block-1)//block)+compressed
            if (magic != 0x9e2a83c1 or not 1 <= block <= 131072 or size != total
                    or not 1 <= size <= 32*1024*1024 or physical != physical_end
                    or (logical_end is not None and logical != logical_end)
                    or physical+stored > len(raw)):
                raise ValueError('PVE_PACKAGE_CHUNK_LAYOUT_INVALID')
            visible = min(4, len(self.header)-descriptor-12)
            if not 0 < visible <= 4 or self.header[descriptor+12:descriptor+12+visible] != struct.pack('<I', stored)[:visible]:
                raise ValueError('PVE_PACKAGE_CHUNK_DESCRIPTOR_INVALID')
            self.chunks.append((descriptor, logical, size, physical, stored, block))
            physical_end, logical_end = physical+stored, logical+size
        self.logical_end = logical_end
        self.tail = raw[physical_end:]
        # 尾部就是完整加密目录；目录最后15字节不属于压缩描述符，逐字节保留。
        if len(self.tail) != 800 or self.tail != raw[-800:] or self.header[785:] != bytes.fromhex('abc14c36')+bytes(11):
            raise ValueError('PVE_PACKAGE_TAIL_INVALID')
        table = self.read(no, ioff-no)
        self.names, position = [], 0
        for _ in range(nc):
            length = struct.unpack_from('<i', table, position)[0]
            position += 4
            if not 1 <= abs(length) <= 2048:
                raise ValueError('PVE_PACKAGE_NAME_INVALID')
            width = 2 if length < 0 else 1
            value = table[position:position+abs(length)*width]
            if len(value) != abs(length)*width or value[-width:] != bytes(width):
                raise ValueError('PVE_PACKAGE_NAME_BOUNDS_INVALID')
            self.names.append(value[:-width].decode('utf-16le' if width==2 else 'utf-8'))
            position += len(value)+8
        if position != len(table):
            raise ValueError('PVE_PACKAGE_NAME_TABLE_INVALID')

    def clear(self, index):
        """只解压所需块，缓存最多整个当前固定包的有限内容。"""
        if index not in self.cache:
            _, _, size, physical, stored, _ = self.chunks[index]
            self.cache[index] = decompress_chunk(io.BytesIO(self.raw),physical,stored,size,len(self.raw))
        return self.cache[index]

    def read(self, offset, size):
        """读取连续逻辑区间；缺块、重叠或超过当前包的区间明确拒绝。"""
        if not 0 < size <= 32*1024*1024 or not 0 <= offset <= self.logical_end-size:
            raise ValueError('PVE_PACKAGE_READ_BOUNDS_INVALID')
        cursor, pieces = offset, []
        for i, (_, logical, length, _, _, _) in enumerate(self.chunks):
            start, end = max(logical,offset), min(logical+length,offset+size)
            if start >= end:
                continue
            if start != cursor:
                raise ValueError('PVE_PACKAGE_READ_GAP')
            pieces.append(self.clear(i)[start-logical:end-logical])
            cursor = end
        if cursor != offset+size:
            raise ValueError('PVE_PACKAGE_READ_INCOMPLETE')
        return b''.join(pieces)

    def end_game_export(self):
        """按真实导出记录边界找到固定EndGame，拒绝索引、名称或父类变化。"""
        position = self.export_offset
        for index in range(1,10285):
            record = self.read(position,68)
            _, _, outer, name_index = struct.unpack_from('<iiii',record)
            size, offset, _, count = struct.unpack_from('<iiii',record,32)
            if not 0 <= count < 10000:
                raise ValueError('PVE_EXPORT_COMPONENT_COUNT_INVALID')
            if index == 10284:
                if not 0 <= name_index < len(self.names) or self.names[name_index] != 'EndGame' or outer != 10425:
                    raise ValueError('PVE_END_GAME_EXPORT_IDENTITY_INVALID')
                return position, outer, size, offset
            position += 68+4*count
        raise ValueError('PVE_END_GAME_EXPORT_MISSING')

    def repack(self, patches, addition):
        """仅重压修改块，同步头尾目录；其他压缩块和未修改的目录字段逐字节保留。"""
        import lzokay
        result = bytearray(self.raw[:self.end])
        header = bytearray(self.header)
        for index, (descriptor, logical, size, physical, stored, block) in enumerate(self.chunks):
            changes = [(at,data) for at,data in patches if logical <= at and at+len(data) <= logical+size]
            extra = addition if index == len(self.chunks)-1 else b''
            chunk = self.raw[physical:physical+stored]
            new_size = size+len(extra)
            if changes or extra:
                clear = bytearray(self.clear(index))
                for at, data in changes:
                    clear[at-logical:at-logical+len(data)] = data
                clear.extend(extra)
                pieces = [lzokay.compress(bytes(clear[i:i+block])) for i in range(0,len(clear),block)]
                chunk = struct.pack('<IIII',0x9e2a83c1,block,sum(map(len,pieces)),len(clear))
                chunk += b''.join(struct.pack('<II',len(p),min(block,len(clear)-i*block)) for i,p in enumerate(pieces))
                chunk += b''.join(pieces)
                if decompress_chunk(io.BytesIO(chunk),0,len(chunk),len(clear),len(chunk)) != clear:
                    raise ValueError('PVE_CHUNK_ROUNDTRIP_FAILED')
            visible = min(4,len(header)-descriptor-12)
            before, after = struct.pack('<I',stored), struct.pack('<I',len(chunk))
            if before[visible:] != after[visible:]:
                raise ValueError('PVE_PARTIAL_DESCRIPTOR_CHANGED')
            struct.pack_into('<II',header,descriptor+4,new_size,len(result))
            header[descriptor+12:descriptor+12+visible] = after[:visible]
            result.extend(chunk)
        encoder = Cipher(algorithms.AES(SCRIPT_RESOURCE_KEY),modes.ECB()).encryptor()
        encrypted = encoder.update(bytes(header))+encoder.finalize()
        result[8:self.end] = encrypted[:self.end-8]
        result.extend(encrypted)
        return bytes(result)


def fix_standalone_return(original):
    """固定原包和函数摘要，保留原胜败/报告流程，仅追加Standalone延迟返厅分支。

    原函数的所有内部跳转保持原偏移，新增条件只跳到新的正常返回位置。使用新导出位置
    保存扩展函数，其他导出逻辑偏移不移动；不改任何进程指令、认证、奖励或就绪标志。
    """
    if hashlib.sha256(original).hexdigest() != SOURCE_SHA256:
        raise ValueError('PVE_SOURCE_FINGERPRINT_MISMATCH')
    package = _Package(original)
    position, outer, size, offset = package.end_game_export()
    function = package.read(offset,size)
    if hashlib.sha256(function).hexdigest() != FUNCTION_SHA256:
        raise ValueError('PVE_END_GAME_FINGERPRINT_MISMATCH')
    virtual_size, stored_size = struct.unpack_from('<II',function,40)
    script = function[48:48+stored_size]
    if (virtual_size,stored_size,size) != (468,316,379) or script[-3:] != b'\x04\x0b\x53':
        raise ValueError('PVE_END_GAME_SCRIPT_LAYOUT_INVALID')
    if package.names[0x1707] != 'ConsoleCommand_Disconnect':
        raise ValueError('PVE_RETURN_FUNCTION_NAME_MISMATCH')
    # 新分支序列化39字节，运行时3个对象引用各扩展4字节，共51字节；目标为返回指令。
    storage_delta = 3+len(STANDALONE_CONDITION)+len(RETURN_CALL)
    virtual_delta = storage_delta+3*4
    target = virtual_size-3+virtual_delta
    insertion = b'\x07'+struct.pack('<H',target)+STANDALONE_CONDITION+RETURN_CALL
    rewritten = bytearray(function[:48]+script[:-3]+insertion+script[-3:]+function[48+stored_size:])
    struct.pack_into('<II',rewritten,40,virtual_size+virtual_delta,stored_size+storage_delta)
    patches = [(position+32,struct.pack('<II',len(rewritten),package.logical_end))]
    result = package.repack(patches,bytes(rewritten))
    checked = _Package(result)
    _, checked_outer, checked_size, checked_offset = checked.end_game_export()
    if (checked_outer,checked_size,checked_offset) != (outer,len(rewritten),package.logical_end):
        raise ValueError('PVE_END_GAME_EXPORT_ROUNDTRIP_FAILED')
    if checked.read(checked_offset,checked_size) != rewritten or checked.header[785:] != package.header[785:]:
        raise ValueError('PVE_RETURN_ROUNDTRIP_FAILED')
    # 完整逻辑块对比：允许的差异仅导出记录8字节和末尾新增函数，原函数仍逐字节存在。
    for i, (_, logical, length, _, _, _) in enumerate(package.chunks):
        expected = bytearray(package.clear(i))
        for at,data in patches:
            if logical <= at and at+len(data) <= logical+length:
                expected[at-logical:at-logical+len(data)] = data
        if checked.read(logical,length) != expected:
            raise ValueError('PVE_UNEXPECTED_LOGICAL_CHANGE')
    return result


def install(guard):
    """只经恢复守卫安装工作副本；调用者必须另行持有启动锁并验证当前构建。"""
    original = (guard.root/RELATIVE).read_bytes()
    variant = fix_standalone_return(original)
    record = guard.apply(RELATIVE,variant)
    return dict(profile=PROFILE,source_sha256=SOURCE_SHA256,variant_sha256=record['variant_sha256'],
                native_live_verified=False,online_branch_unchanged=True)
