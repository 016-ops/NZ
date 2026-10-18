"""修复本人单机B键换包入口；保留地图、时间、射击状态及百宝箱的原生换包资格。"""
import hashlib
import struct
from . import native_owned_loadout_compat as owned
from .native_hunt_compat import _Expr,_field,_reference,_context,_native

PROFILE='cn2020-standalone-owned-bag-v4-candidate'
RELATIVE=owned.RELATIVE
SOURCE_SHA256=owned.SOURCE_SHA256
FUNCTION='TGPlayerController.ShowChangeBag'
FUNCTION_SHA256='9a44cb24b09122fe957f3bbb75cf6faffb395f22ce74a40ffaf73695a6d54005'
CANDIDATE_SHA256='dc38b18a621ccf471a526178f23de96397a215ebd178de11dddf35d338f2b708'


def _require(value,code):
    """原包、函数布局和输出固定摘要失配必须失败，不提供跳过验证开关。"""
    if not value:raise ValueError('NATIVE_BAG_'+code)


def _modified(package):
    """在保持原VM地址的跳板中增加本地人类分支；在线仍执行完整原函数。"""
    record,raw=package.export(FUNCTION)
    _require(hashlib.sha256(raw).hexdigest()==FUNCTION_SHA256,'FUNCTION_CHANGED')
    virtual,stored=struct.unpack_from('<II',raw,40);script=raw[48:48+stored]
    _require((virtual,stored,len(raw))==(277,197,260) and
             script[:16]==bytes.fromhex('0f00050f00002e006d0000016bf3ffff') and
             script[-3:]==b'\x04\x0b\x53','LAYOUT_CHANGED')
    refs=package.refs
    def prop(obj,identity):
        """上下文引用和VM宽度来自固定原包，不新增或猜测对象属性。"""
        ref=refs[identity]
        return _context(obj,_field(ref),ref)
    def call(name):
        """只调用当前名称表中既有的本地控制器和Pawn方法。"""
        return _Expr(b'\x1b'+struct.pack('<II',package.names.index(name),0)+b'\x16')
    world=prop(_field(refs['Engine.Actor.WorldInfo']),'Engine.WorldInfo.NetMode')
    standalone=_native(154,_Expr(b'\x38\x3a')+world,_Expr(b'\x38\x3a\x24\0'))
    pawn=_Expr(b'\x2e')+_reference(refs['TGPawn'])+_field(refs['Engine.Controller.Pawn'])
    parts=[];branches=[];cursor=virtual-1
    def emit(expr):
        """独立累计序列化与VM偏移，UObject引用展开为八字节。"""
        nonlocal cursor
        parts.append(expr);cursor+=expr.virtual
    for condition in (standalone,call('IsLocalPlayerController'),
                      _native(119,pawn,_Expr(b'\x2a')),_context(pawn,call('CanChangeBag'))):
        branches.append(len(parts));emit(_Expr(b'\x07\0\0')+condition)
    # 保留原生CanChangeBag，不能因为是验收账号就允许在非法区域或射击中换包。
    emit(_context(pawn,call('ShowChangeBag')));emit(_Expr(b'\x04\x0b'))
    fallback=cursor
    emit(_Expr(script[:16],28));emit(_Expr(b'\x06'+struct.pack('<H',28)));emit(_Expr(b'\x53'))
    _require(cursor<65536,'VM_LIMIT')
    for index in branches:
        expr=parts[index];parts[index]=_Expr(b'\x07'+struct.pack('<H',fallback)+expr.data[3:],expr.virtual)
    code=b'\x06'+struct.pack('<H',virtual-1)+b'\x0b'*25+script[16:-1]+b''.join(x.data for x in parts)
    result=bytearray(raw[:48]+code+raw[48+stored:])
    struct.pack_into('<II',result,40,cursor,len(code))
    return record,bytes(result)


def _build(original):
    """从固定原件组合已验证配装v3及新增换包入口，不修改磁盘或启动进程。"""
    _require(isinstance(original,bytes) and hashlib.sha256(original).hexdigest()==SOURCE_SHA256,'SOURCE_CHANGED')
    baseline=owned.fix_standalone_owned_loadout(original)
    package=owned._Package(baseline);record,modified=_modified(package)
    result=package.repack([(record['record']+32,struct.pack('<II',len(modified),package.logical_end))],modified)
    checked=owned._Package(result)
    _require(checked.refs==package.refs and checked.export(FUNCTION)[1]==modified,'ROUNDTRIP_FAILED')
    return result


def fix_standalone_bag(original):
    """只返回摘要已冻结的合并候选，禁止把未知运行包当成可增量修改的基础。"""
    result=_build(original)
    _require(hashlib.sha256(result).hexdigest()==CANDIDATE_SHA256,'CANDIDATE_CHANGED')
    return result


def install_candidate(guard):
    """由外部限时Launcher负责工作副本安装、退出恢复和清理，不写原客户端。"""
    record=guard.apply(RELATIVE,fix_standalone_bag((guard.root/RELATIVE).read_bytes()))
    return dict(profile=PROFILE,source_sha256=SOURCE_SHA256,variant_sha256=record['variant_sha256'],
                requires_bounded_trial=True,online_branch_unchanged=True,native_live_verified=False)
