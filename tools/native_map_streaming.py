"""只读盘点当前主图中的原生流送包引用，补充普通导入表遗漏的动态关卡。

本模块只证明属性被有界解析；不证明资源闭包、原生加载、地图玩法或难度通过。
仅接受已核验的未压缩或LZO区间，未知属性明确失败，不能把失败变成空依赖。
"""
from functools import lru_cache
from pathlib import Path
import hashlib
import re
import struct

from tools.native_package_dependencies import (
    Cipher, algorithms, modes, SCRIPT_RESOURCE_KEY, PackageInspectionError,
    exact_read, import_table, decompress_chunk, MAX_TABLE_BYTES, MAX_CHUNK_BYTES,
)
from tools.client_catalog import RESOURCE_KEY


def _require(condition, code):
    """使用固定错误码拒绝损坏表或未知格式，不回显地图正文。"""
    if not condition:
        raise PackageInspectionError('STREAMING_'+code)


def _properties(data, names):
    """从非Actor流送对象的4字节网络索引后读取完整标签，返回包名及初始加载标志。"""
    position, values, terminated = 4, {}, False

    def fname(at):
        """FName必须位于当前包名称表中，数字后缀不能丢弃。"""
        _require(at+8 <= len(data), 'FNAME_TRUNCATED')
        index, number = struct.unpack_from('<II', data, at)
        _require(index < len(names) and number < 1000000, 'FNAME_INVALID')
        return names[index]+('_'+str(number-1) if number else '')

    for _ in range(4096):
        name = fname(position)
        position += 8
        if name == 'None':
            terminated = True
            break
        kind = fname(position)
        position += 8
        _require(position+8 <= len(data), 'PROPERTY_TAG_TRUNCATED')
        length, array = struct.unpack_from('<II', data, position)
        position += 8
        _require(array < 1000000, 'PROPERTY_ARRAY_INVALID')
        # UE3的Struct/Byte标签还有类型名，Bool值存于标签，不能仅按Length跳过。
        if kind in ('StructProperty', 'ByteProperty'):
            fname(position)
            position += 8
        _require(kind in ('StructProperty','ByteProperty','BoolProperty','NameProperty','ObjectProperty',
                         'IntProperty','FloatProperty','ArrayProperty','StrProperty','ClassProperty'),
                 'PROPERTY_KIND_UNVERIFIED')
        if kind == 'BoolProperty':
            _require(length == 0 and position < len(data) and data[position] in (0,1), 'BOOL_TAG_INVALID')
            value = bool(data[position])
            position += 1
        else:
            _require(length <= len(data)-position, 'PROPERTY_VALUE_TRUNCATED')
            value = fname(position) if kind == 'NameProperty' and length == 8 else None
        if name in ('PackageName','PackageNameToLoad','bShouldBeLoaded','bShouldBeVisible',
                    'bInitiallyLoaded','bInitiallyVisible'):
            _require(name not in values and array == 0, 'DUPLICATE_DEPENDENCY_PROPERTY')
            if name.startswith('Package'):
                _require(kind == 'NameProperty' and length == 8, 'PACKAGE_NAME_TYPE_INVALID')
                _require(value == 'None' or re.fullmatch(r'[A-Za-z0-9_\-]+', value), 'PACKAGE_NAME_INVALID')
            values[name] = value
        position += length
    _require(terminated and position == len(data), 'PROPERTY_STREAM_NOT_COMPLETE')
    return values


def _logical_reader(stream, header, delta, end, size, compression):
    """提供有界逻辑读取；LZO块逐个核对并最多缓存两块，不按地图总大小分配内存。"""
    if compression == 0:
        def plain(offset, count):
            """未压缩包直接按已验证的物理区间读取。"""
            return exact_read(stream,offset,count,size)
        plain.limit = size
        return plain
    _require(compression == 2 and 113+delta <= len(header), 'COMPRESSION_UNVERIFIED')
    count = struct.unpack_from('<I',header,109+delta)[0]
    _require(1 <= count <= 4096, 'CHUNK_COUNT_INVALID')
    chunks, previous_logical_end, previous_physical_end = [], None, end
    for index in range(count):
        at = 113+delta+16*index
        _require(at+12 <= len(header), 'CHUNK_DIRECTORY_TRUNCATED')
        logical,unpacked,physical = struct.unpack_from('<III',header,at)
        magic,block,compressed,total = struct.unpack('<IIII',exact_read(stream,physical,16,size))
        _require(magic == 0x9e2a83c1 and 1 <= block <= 131072 and 1 <= unpacked <= MAX_CHUNK_BYTES
                 and total == unpacked, 'CHUNK_HEADER_INVALID')
        stored = 16+8*((unpacked+block-1)//block)+compressed
        visible = min(4,len(header)-at-12)
        _require(header[at+12:at+12+visible] == struct.pack('<I',stored)[:visible], 'CHUNK_STORED_SIZE_MISMATCH')
        _require(physical == previous_physical_end and 0 < stored <= MAX_CHUNK_BYTES
                 and physical+stored <= size and (previous_logical_end is None or logical == previous_logical_end),
                 'CHUNK_RANGE_GAP_OR_OVERLAP')
        chunks.append((logical,unpacked,physical,stored))
        previous_logical_end,previous_physical_end = logical+unpacked,physical+stored

    @lru_cache(maxsize=2)
    def clear(index):
        """只解压覆盖所需表或流送对象的块，所有子块边界仍由公共LZO校验器检查。"""
        logical,unpacked,physical,stored = chunks[index]
        return decompress_chunk(stream,physical,stored,unpacked,size)

    def compressed_read(offset, count):
        """将一个有限逻辑区间拼接完整；缺块或重叠均拒绝，不补零推测内容。"""
        _require(0 <= offset and 0 < count <= MAX_CHUNK_BYTES and offset+count <= previous_logical_end,
                 'LOGICAL_READ_BOUNDS_INVALID')
        cursor,pieces = offset,[]
        for index,(logical,length,physical,stored) in enumerate(chunks):
            begin,stop = max(offset,logical),min(offset+count,logical+length)
            if begin >= stop:
                continue
            _require(begin == cursor, 'LOGICAL_READ_GAP')
            pieces.append(clear(index)[begin-logical:stop-logical])
            cursor = stop
        _require(cursor == offset+count, 'LOGICAL_READ_INCOMPLETE')
        return b''.join(pieces)
    compressed_read.limit = previous_logical_end
    return compressed_read


def inspect_streaming(path):
    """有界读取已由导入盘点核验的包，逐个解析真实LevelStreaming类导出。

    文件摘要/大小/时间在读取前后再次核对，任何中途变化使整个结果失败。
    不搜索任意正文文本，不依文件名猜测动态包名，也不自动启动游戏。
    """
    path = Path(path)
    imports = import_table(path)
    before = path.stat()
    with path.open('rb') as stream:
        tag, end = struct.unpack('<II', exact_read(stream,0,8,before.st_size))
        key = RESOURCE_KEY if tag == 0x3e27a8ff else SCRIPT_RESOURCE_KEY
        encrypted = exact_read(stream,8,(end-8)//16*16,before.st_size)
        decoder = Cipher(algorithms.AES(key),modes.ECB()).decryptor()
        header = decoder.update(encrypted)+decoder.finalize()
        # 当前原包可能在尾部保存完整AES目录；只有与首部密文前缀逐字节一致才采用。
        # 没有镜像的独立夹具仍可用完整可见描述符核验，不猜测被截断的目录字段。
        mirror_size = ((end-8+15)//16)*16
        if mirror_size <= before.st_size:
            mirror = exact_read(stream,before.st_size-mirror_size,mirror_size,before.st_size)
            if mirror[:len(encrypted)] == encrypted:
                decoder = Cipher(algorithms.AES(key),modes.ECB()).decryptor()
                header = decoder.update(mirror)+decoder.finalize()
        delta = struct.unpack_from('<i',header,8)[0]-5
        nc,no,ec,eo,ic,ioff = struct.unpack_from('<IIIIII',header,21+delta)
        read = _logical_reader(stream,header,delta,end,before.st_size,imports['compression'])
        names = []
        name_data = read(no,ioff-no)
        cursor = 0
        for _ in range(nc):
            _require(cursor+4 <= len(name_data), 'NAME_LENGTH_TRUNCATED')
            length = struct.unpack_from('<i',name_data,cursor)[0]
            cursor += 4
            width = 2 if length < 0 else 1
            size = abs(length)*width
            _require(1 <= abs(length) <= 1024 and cursor+size+8 <= len(name_data), 'NAME_BOUNDS_INVALID')
            text = name_data[cursor:cursor+size].decode('utf-16le' if width == 2 else 'utf-8')
            _require(text.endswith('\0') and '\0' not in text[:-1], 'NAME_TERMINATOR_INVALID')
            names.append(text[:-1])
            cursor += size+8
        _require(cursor == len(name_data), 'NAME_TABLE_END_MISMATCH')
        rows, position, dependencies = [], eo, set()
        for index in range(ec):
            _require(position-eo < MAX_TABLE_BYTES, 'EXPORT_TABLE_TOO_LARGE')
            record = read(position,68)
            cl,parent,outer,name,number = struct.unpack_from('<iiiii',record)
            size,offset,flags,count = struct.unpack_from('<iiii',record,32)
            _require(0 <= name < nc and 0 <= number < 1000000 and 0 <= count < 10000
                     and size >= 0 and 0 <= offset <= read.limit-size, 'EXPORT_RECORD_INVALID')
            position += 68+count*4
            if cl >= 0:
                continue
            _require(-cl <= len(imports['imports']), 'CLASS_REFERENCE_INVALID')
            cls = imports['imports'][-cl-1]
            if not (cls['class_package'] == 'Core' and cls['class_name'] == 'Class'
                    and cls['object_name'].startswith('LevelStreaming')):
                continue
            _require(size <= 1024*1024, 'OBJECT_TOO_LARGE')
            values = _properties(read(offset,size),names)
            packages = [values[key] for key in ('PackageName','PackageNameToLoad')
                        if values.get(key) not in (None,'None')]
            dependencies.update(packages)
            rows.append(dict(export_index=index+1,name=names[name]+('_'+str(number-1) if number else ''),
                             class_name=cls['object_name'],properties=values,
                             package_name_status='PARSED' if packages else 'MISSING_REQUIRES_RULE_CHECK'))
        stream.seek(0)
        sha = hashlib.file_digest(stream,'sha256').hexdigest()
    after = path.stat()
    _require((before.st_size,before.st_mtime_ns) == (after.st_size,after.st_mtime_ns)
             and sha == imports['sha256'], 'SOURCE_CHANGED_DURING_READ')
    return dict(kind='STATIC_STREAMING_REFERENCES_NOT_RESOURCE_CLOSURE',sha256=sha,
                compression=imports['compression'],
                status='STREAMING_PROPERTIES_PARSED',objects=rows,root_packages=imports['root_packages'],
                streaming_packages=sorted(dependencies),dynamic_streaming_refs_parsed=True,
                dependency_closure_verified=False,native_loaded=False,client_writes=0)
