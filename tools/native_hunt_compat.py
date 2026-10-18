"""当前构建猎场单机出生时序候选；只生成有固定摘要的变体，不自动启用或发布。

大都会的原生 Pawn 早于初始碰撞关卡出生并跌落。本候选只在 Standalone、没有
Pawn、真实向下碰撞检测尚未命中时，延迟原 RestartPlayer；保留原生成流程及在线
分支。仅地图 113 有两次实机证据，其他地图、缺地板超时和完整玩法尚未验收。
"""
import hashlib
import struct

from tools.native_package_dependencies import Cipher, algorithms, modes, SCRIPT_RESOURCE_KEY


PROFILE = 'cn2020-standalone-hunt-spawn-floor-v1-candidate'
RELATIVE = 'TGame/CookedPC/TGADBase.u'
SOURCE_SHA256 = '2c60116b91a6f95127e274f6a5e1dcb3c888ff8ae6d807cbd8e63969fe16106e'
FUNCTION_SHA256 = 'df04553adf6f736e6d8704c8f79f0b30d24c2b918183f7ecc56d791ea9956fd1'
CANDIDATE_SHA256 = 'f6741534475f1c07d743407b535d8b947dffe06958b479c89b311b3934a68c2b'


def _require(condition, code):
    """检查失败即拒绝生成；使用显式异常，Python 优化模式也不能移除安全边界。"""
    if not condition:
        raise ValueError('HUNT_SPAWN_' + code)


class _Package:
    """只读当前未压缩包的目录、Outer 身份和函数；不接受其他布局或任意大文件。"""

    def __init__(self, raw, *, reference_aliases=()):
        """有界解码头尾相同目录，验证表和导出区间；损坏引用必须明确失败。"""
        _require(isinstance(raw, bytes) and 128 < len(raw) < 8*1024*1024, 'SIZE_INVALID')
        self.raw = raw
        tag, self.end = struct.unpack_from('<II', raw)
        _require(tag == 0x3e27a900 and self.end == 125, 'HEADER_INVALID')
        self.footer_size = 128
        _require(raw[8:self.end] == raw[-128:][:self.end-8], 'DIRECTORY_MIRROR_MISMATCH')
        decoder = Cipher(algorithms.AES(SCRIPT_RESOURCE_KEY), modes.ECB()).decryptor()
        self.header = decoder.update(raw[-128:]) + decoder.finalize()
        delta = struct.unpack_from('<i', self.header, 8)[0]-5
        _require(struct.unpack_from('<I', self.header)[0] == 0x202fc and delta == 0, 'SUMMARY_INVALID')
        _require(struct.unpack_from('<I', self.header, 105)[0] == 0, 'COMPRESSION_UNSUPPORTED')
        nc, no, ec, eo, ic, ioff = struct.unpack_from('<IIIIII', self.header, 21)
        _require(0 < nc < 100000 and 0 < ec < 100000 and 0 < ic < 100000, 'TABLE_COUNT_INVALID')
        _require(self.end <= no < ioff <= eo < len(raw)-128 and ioff+ic*28 <= eo, 'TABLE_BOUNDS_INVALID')
        self.names, position = [], no
        for _ in range(nc):
            _require(position+4 <= ioff, 'NAME_LENGTH_TRUNCATED')
            length = struct.unpack_from('<i', raw, position)[0]
            position += 4
            width = 2 if length < 0 else 1
            size = abs(length)*width
            _require(1 <= abs(length) <= 2048 and position+size+8 <= ioff, 'NAME_BOUNDS_INVALID')
            value = raw[position:position+size]
            _require(value[-width:] == bytes(width), 'NAME_TERMINATOR_INVALID')
            self.names.append(value[:-width].decode('utf-16le' if width == 2 else 'utf-8'))
            position += size+8
        _require(position == ioff, 'NAME_TABLE_INVALID')
        self.imports = [struct.unpack_from('<iiiiiii', raw, ioff+i*28) for i in range(ic)]
        self.exports, position = [], eo
        for index in range(ec):
            _require(position+68 <= len(raw)-128, 'EXPORT_TRUNCATED')
            cl, parent, outer, name, number = struct.unpack_from('<iiiii', raw, position)
            size, offset, flags, count = struct.unpack_from('<iiii', raw, position+32)
            _require(0 <= name < nc and 0 <= count < 10000 and size >= 0
                     and self.end <= offset <= len(raw)-128-size, 'EXPORT_BOUNDS_INVALID')
            self.exports.append(dict(index=index+1, cl=cl, outer=outer, name=self.names[name],
                                     number=number, size=size, offset=offset, record=position))
            position += 68+count*4
        self.refs = {};seen_aliases=set()
        for index in range(-ic, ec+1):
            if index:
                identity = self.identity(index)
                if identity in self.refs:
                    # 旧ATDGame同一自身类同时有导入/导出。仅调用方明确给出完整三元组才允许
                    # 该固定别名，默认仍拒绝任何重复；函数/属性不能靠名字覆盖未知引用。
                    alias=(identity,self.refs[identity],index)
                    _require(alias in reference_aliases, 'DUPLICATE_OBJECT_IDENTITY')
                    seen_aliases.add(alias)
                self.refs[identity] = index
        _require(seen_aliases==set(reference_aliases),'REFERENCE_ALIAS_SET_MISMATCH')

    def identity(self, index, depth=0):
        """解析最多 64 层有效 Outer 链；不猜测失效名称或循环中的属性索引。"""
        _require(depth < 64 and index != 0, 'REFERENCE_CYCLE_OR_NULL')
        if index < 0:
            _require(-index <= len(self.imports), 'IMPORT_REFERENCE_INVALID')
            row = self.imports[-index-1]
            _require(0 <= row[5] < len(self.names), 'IMPORT_NAME_INVALID')
            outer, name, number = row[4], self.names[row[5]], row[6]
        else:
            _require(index <= len(self.exports), 'EXPORT_REFERENCE_INVALID')
            row = self.exports[index-1]
            outer, name, number = row['outer'], row['name'], row['number']
        # FName 的数字是对象身份的一部分；组件 foo_0 与 foo_1 不是重复对象。
        _require(number >= 0, 'OBJECT_NAME_NUMBER_INVALID')
        if number:
            name += '_'+str(number-1)
        return (self.identity(outer, depth+1)+'.' if outer else '')+name

    def export(self, identity):
        """按完整身份读取唯一导出，返回目录记录和未经修改的函数字节。"""
        index = self.refs.get(identity, 0)
        _require(index > 0, 'FUNCTION_MISSING')
        row = self.exports[index-1]
        return row, self.raw[row['offset']:row['offset']+row['size']]


class _Expr:
    """同时保存磁盘字节和 VM 长度；对象引用加载后从 4 字节扩展至 8 字节。"""

    def __init__(self, data, virtual=None):
        """保存有限候选的片段，不解析或执行传入的任何游戏函数。"""
        self.data = bytes(data)
        self.virtual = len(self.data) if virtual is None else virtual

    def __add__(self, other):
        """顺序合并片段，磁盘偏移和 VM 偏移始终分别累加。"""
        return _Expr(self.data+other.data, self.virtual+other.virtual)


def _reference(index):
    """编码已由包元数据核实的对象或属性引用，不接受外部提供的地址。"""
    return _Expr(struct.pack('<i', index), 8)


def _field(index, local=False):
    """局部参数和实例属性分别使用原生 LocalVariable/InstanceVariable 操作码。"""
    return _Expr(bytes([0 if local else 1]))+_reference(index)


def _context(obj, inner, result=0):
    """空对象跳过内部表达式；跳过长度必须使用展开后的 VM 长度。"""
    return _Expr(b'\x19')+obj+_Expr(struct.pack('<H', inner.virtual))+_reference(result)+_Expr(b'\0')+inner


def _native(index, *args):
    """只组装当前构建已核实的原生函数调用；548 为真实世界几何 FastTrace。"""
    result = _Expr(bytes([index]) if index < 256 else bytes([0x60+(index >> 8), index & 255]))
    for arg in args:
        result = result+arg
    return result+_Expr(b'\x16')


def fix_standalone_spawn_floor(original):
    """精确原包及函数摘要匹配后生成与第 67 轮实机相同的候选；不安装、不改内存。

    追加跳板保留原函数全部既有 VM 分支地址。在线或已有 Pawn 时立即执行原 Super；
    缺出生点沿用原失败路径。没有地板仍等待，因此此版本仅供限时受控实验。
    """
    _require(isinstance(original, bytes) and hashlib.sha256(original).hexdigest() == SOURCE_SHA256,
             'SOURCE_FINGERPRINT_MISMATCH')
    package = _Package(original)
    record, function = package.export('TGADBaseGame.RestartPlayer')
    _require(hashlib.sha256(function).hexdigest() == FUNCTION_SHA256, 'FUNCTION_FINGERPRINT_MISMATCH')
    virtual, stored = struct.unpack_from('<II', function, 40)
    script = function[48:48+stored]
    _require((virtual, stored) == (211, 139) and script[:11] == bytes.fromhex('1c9af9ffff00a110000016')
             and script[-3:] == b'\x04\x0b\x53', 'FUNCTION_LAYOUT_INVALID')
    refs = package.refs
    param = _field(refs['TGADBaseGame.RestartPlayer.NewPlayer'], True)

    def named(name, *args):
        """虚函数名必须已经存在；不追加名称、导入项或类成员。"""
        result = _Expr(b'\x1b'+struct.pack('<II', package.names.index(name), 0))
        for arg in args:
            result = result+arg
        return result+_Expr(b'\x16')

    def prop(obj, identity):
        """仅从当前包的真实属性身份构造空值安全的实例字段读取。"""
        index = refs[identity]
        return _context(obj, _field(index), index)

    spot = prop(param, 'Engine.Controller.StartSpot')
    location = prop(spot, 'Engine.Actor.Location')
    standalone = _native(154, _Expr(b'\x38\x3a')+prop(_field(refs['Engine.Actor.WorldInfo']),
                         'Engine.WorldInfo.NetMode'), _Expr(b'\x38\x3a\x24\0'))
    pawn_none = _native(114, prop(param, 'Engine.Controller.Pawn'), _Expr(b'\x2a'))
    has_spot = _native(119, spot, _Expr(b'\x2a'))
    no_floor = _native(548, _native(216, location, _Expr(b'\x23'+struct.pack('<fff', 0., 0., 256.))), location)
    restart = _Expr(b'\x21'+struct.pack('<II', package.names.index('ServerRestartPlayer'), 0))
    retry = _context(param, _native(280, _Expr(b'\x1e'+struct.pack('<f', .25)), _Expr(b'\x27'), restart))
    clear = _context(param, named('ClearTimer', restart))
    parts, labels, fixups, cursor = [], {}, [], virtual-1

    def emit(expr):
        """顺序生成追加区，并记录此构建的 VM 指针展开长度。"""
        nonlocal cursor
        parts.append(expr)
        cursor += expr.virtual

    def label(name):
        """每个跳转目标只允许定义一次，避免同名覆盖导致意外分支。"""
        _require(name not in labels, 'DUPLICATE_LABEL')
        labels[name] = cursor

    def branch(target, condition=None):
        """固定宽度的分支先占位，完成所有 VM 布局后回填精确目标。"""
        fixups.append((len(parts), target))
        emit(_Expr(bytes([7 if condition is not None else 6])+b'\0\0')
             +(condition if condition is not None else _Expr(b'')))

    branch('original', standalone)
    branch('original', pawn_none)
    emit(_Expr(b'\x0f')+spot+named('FindPlayerStart', param))
    branch('clear_then_original', has_spot)
    branch('clear_then_original', no_floor)
    emit(retry)
    emit(_Expr(b'\x04\x0b'))
    label('clear_then_original')
    emit(clear)
    label('original')
    emit(_Expr(script[:11], 19))
    emit(_Expr(b'\x06'+struct.pack('<H', 19)))
    emit(_Expr(b'\x53'))
    for index, target in fixups:
        expr = parts[index]
        _require(0 < labels[target] < 65536, 'BRANCH_BOUNDS_INVALID')
        parts[index] = _Expr(expr.data[:1]+struct.pack('<H', labels[target])+expr.data[3:], expr.virtual)
    guard = _Expr(b'')
    for expr in parts:
        guard = guard+expr
    # 首个 Super 调用的 19 字节 VM 空间改为跳转和不可达填充；后面的原 VM 地址不变。
    code = b'\x06'+struct.pack('<H', virtual-1)+b'\x0b'*16+script[11:-1]+guard.data
    rewritten = bytearray(function[:48]+code+function[48+stored:])
    struct.pack_into('<II', rewritten, 40, virtual-1+guard.virtual, len(code))
    append_at = len(original)-package.footer_size
    result = bytearray(original[:append_at])+rewritten+original[append_at:]
    struct.pack_into('<II', result, record['record']+32, len(rewritten), append_at)
    checked = _Package(bytes(result))
    _, new_function = checked.export('TGADBaseGame.RestartPlayer')
    _require(new_function == rewritten and checked.refs == refs, 'FUNCTION_ROUNDTRIP_FAILED')
    expected = bytearray(original[:append_at])
    struct.pack_into('<II', expected, record['record']+32, len(rewritten), append_at)
    _require(result[:append_at] == expected and result[-128:] == original[-128:], 'UNEXPECTED_PACKAGE_CHANGE')
    _require(hashlib.sha256(result).hexdigest() == CANDIDATE_SHA256, 'CANDIDATE_FINGERPRINT_MISMATCH')
    return bytes(result)


def install_candidate(guard):
    """只经持久恢复守卫安装固定候选；调用方另行负责工作副本、启动锁及限时实验。

    此函数没有被默认 Launcher 调用；回执明确保留实验状态，不将两次出生等同全图通过。
    """
    target = guard.root/RELATIVE
    _require(128 < target.stat().st_size < 8*1024*1024, 'SIZE_INVALID')
    variant = fix_standalone_spawn_floor(target.read_bytes())
    record = guard.apply(RELATIVE, variant)
    return dict(profile=PROFILE, source_sha256=SOURCE_SHA256, variant_sha256=record['variant_sha256'],
                native_live_verified=False, playable_map_accepted=False, online_branch_unchanged=True,
                limited_live_map_ids=[113], requires_bounded_trial=True)
