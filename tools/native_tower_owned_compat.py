"""当前塔防单机权益候选：刷新本人百宝箱效果并关闭三段无条件调试附送。

仅修改固定TGATDBase.AddAllFunctionalItem，不生成资产、不增加金币或陷阱等级。
正常物品循环、专家/金币卡和所有在线逻辑继续沿原生函数；默认Launcher尚不安装。
"""
import hashlib
import struct

from tools.native_hunt_compat import _Package, _Expr, _reference, _field, _context, _native

PROFILE='cn2020-standalone-tower-owned-functional-v1-candidate'
RELATIVE='TGame/CookedPC/TGATDBase.u'
SOURCE_SHA256='bb2514e4c56fc8f0236b9df55889c0357c5f58ade00abcec4897bcc09ec771a9'
FUNCTION_SHA256='b490ff40b96dde058af07b52fc87f1b88acbd36f74ca4dd69c5548bf74e53797'
CANDIDATE_SHA256='74cc991c9d4a7086e4aac54516b8d43d7cf612b2619c03a02a91845ed15cddaa'
FUNCTION='TGATDBasePlayerInventoryManager.AddAllFunctionalItem'
# 三处判断均由真实类属性及原生int相等指令核对，分别为全能/资金、付费陷阱、皮肤调试块。
DEBUG_CONDITIONS=((655,1051),(993,1427),(2012,3041))


def _require(condition,code):
    """固定源和结构失配直接拒绝，优化模式下也不移除检查。"""
    if not condition:
        raise ValueError('TOWER_OWNED_'+code)


def _modified_function(package, *, function=FUNCTION, function_sha=FUNCTION_SHA256,
                       layout=(3211,2327,2390), conditions=DEBUG_CONDITIONS,
                       pri_class='TGATDBasePlayerReplicationInfo',
                       super_prefix=bytes.fromhex('1c81f9ffff007631000016')):
    """保持原VM地址，替换三条条件并追加本人百宝箱刷新跳板；其余指令逐字节保留。"""
    record,raw=package.export(function)
    _require(hashlib.sha256(raw).hexdigest()==function_sha,'FUNCTION_FINGERPRINT_MISMATCH')
    virtual,stored=struct.unpack_from('<II',raw,40);script=raw[48:48+stored]
    _require((virtual,stored,len(raw))==layout and script[-3:]==b'\x04\x0b\x53'
             and script[:11]==super_prefix and len(super_prefix)==11,'FUNCTION_LAYOUT_INVALID')
    refs=package.refs

    def prop(obj,identity):
        """只读取此原包已经声明的字段，Context保留真实引用和VM跳过量。"""
        index=refs[identity]
        return _context(obj,_field(index),index)

    world=prop(_field(refs['Engine.Actor.WorldInfo']),'Engine.WorldInfo.NetMode')
    standalone=_native(154,_Expr(b'\x38\x3a')+world,_Expr(b'\x38\x3a\x24\0'))
    _require(len(standalone.data)==26 and standalone.virtual==38,'CONDITION_LAYOUT_INVALID')
    pieces=[];cursor=0
    for at,target in conditions:
        _require(script[at-3:at]==b'\x07'+struct.pack('<H',target)
                 and script[at:at+26]==standalone.data,'DEBUG_CONDITION_CHANGED')
        pieces.extend((script[cursor:at],b'\x28'+b'\x0b'*37));cursor=at+26
    pieces.append(script[cursor:]);without_debug=b''.join(pieces)
    # False只跳过原本仅单机触发的调试块；在线原本条件也为false，原有物品授权循环不改。
    parts=[];fallbacks=[];vm=virtual-1

    def emit(expr):
        """分别累计序列化长度和展开后的VM长度，避免跳转落入对象引用中间。"""
        nonlocal vm
        parts.append(expr);vm+=expr.virtual

    def guard(condition):
        """非本地人类或数据缺失直接回到原Super首指令，不写任何新增状态。"""
        fallbacks.append(len(parts));emit(_Expr(b'\x07\0\0')+condition)

    pri=_field(refs[function+'.PRI'],True)
    derived=_Expr(b'\x2e')+_reference(refs[pri_class])+pri
    owner=_Expr(b'\x2e')+_reference(refs['UTGame.TGPlayerController'])+prop(pri,'Engine.Actor.Owner')
    guard(standalone);guard(_native(119,pri,_Expr(b'\x2a')))
    guard(_native(119,derived,_Expr(b'\x2a')));guard(_native(119,owner,_Expr(b'\x2a')))
    # 本包没有IsLocalPlayerController名称；使用原PlayerController的实际Player对象，排除AI/无本地玩家控制器。
    guard(_native(119,prop(owner,'Engine.PlayerController.Player'),_Expr(b'\x2a')))
    refresh=_Expr(b'\x1c')+_reference(refs[pri_class+'.GetPVEGateChestInfo'])+_Expr(b'\x16')
    emit(_context(derived,refresh))
    # 此时v3已读本人有效等级；调用原方法同时更新陷阱上限和随时出售权限，非自行加常量。
    fallback=vm
    emit(_Expr(script[:11],19));emit(_Expr(b'\x06'+struct.pack('<H',19)));emit(_Expr(b'\x53'))
    _require(vm<65536,'VM_LIMIT')
    for index in fallbacks:
        expr=parts[index];parts[index]=_Expr(b'\x07'+struct.pack('<H',fallback)+expr.data[3:],expr.virtual)
    code=b'\x06'+struct.pack('<H',virtual-1)+b'\x0b'*16+without_debug[11:-1]+b''.join(p.data for p in parts)
    rewritten=bytearray(raw[:48]+code+raw[48+stored:]);struct.pack_into('<II',rewritten,40,vm,len(code))
    return record,bytes(rewritten)


def fix_standalone_tower_owned(original):
    """只生成固定源候选；原区仅一个导出指针变化，包尾目录和全部其他原字节保留。"""
    _require(isinstance(original,bytes) and hashlib.sha256(original).hexdigest()==SOURCE_SHA256,'SOURCE_FINGERPRINT_MISMATCH')
    package=_Package(original);record,rewritten=_modified_function(package)
    append_at=len(original)-package.footer_size
    result=bytearray(original[:append_at])+rewritten+original[append_at:]
    struct.pack_into('<II',result,record['record']+32,len(rewritten),append_at)
    verified=_Package(bytes(result));_,actual=verified.export(FUNCTION)
    _require(actual==rewritten and verified.refs==package.refs,'FUNCTION_ROUNDTRIP_FAILED')
    expected=bytearray(original[:append_at]);struct.pack_into('<II',expected,record['record']+32,len(rewritten),append_at)
    _require(result[:append_at]==expected and result[-128:]==original[-128:],'UNEXPECTED_PACKAGE_CHANGE')
    _require(hashlib.sha256(result).hexdigest()==CANDIDATE_SHA256,'CANDIDATE_FINGERPRINT_MISMATCH')
    return bytes(result)


def install_candidate(guard):
    """通过持久恢复守卫安装固定候选，外层必须负责限时、工作副本、会话撤销及结果回读。"""
    target=guard.root/RELATIVE
    _require(128<target.stat().st_size<8*1024*1024,'SIZE_INVALID')
    record=guard.apply(RELATIVE,fix_standalone_tower_owned(target.read_bytes()))
    return dict(profile=PROFILE,source_sha256=SOURCE_SHA256,variant_sha256=record['variant_sha256'],
                requires_bounded_trial=True,native_live_verified=False,asset_changes=False,online_branch_unchanged=True)
