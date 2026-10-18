"""空间站等旧塔防的本人权益候选；固定原包，只刷新百宝箱并关闭三段单机调试附送。"""
import hashlib,struct
from .native_hunt_compat import _Package
from .native_tower_owned_compat import _modified_function

PROFILE='cn2020-standalone-legacy-tower-owned-v1-candidate'
RELATIVE='TGame/CookedPC/ATDGame.u'
SOURCE_SHA256='2415be8948ef9b345377cabce0baec6974eb6226bac9110c5747c93b2493b65e'
FUNCTION='ATDPlayerInventoryManager.AddAllFunctionalItem'
FUNCTION_SHA256='9ac4906bcfbb1d7a9a78afd343bfc160713852c450682a7e5b9e23ce692ce3e7'
CANDIDATE_SHA256='714039be5a370680dd7b67683b7e92b9693a5b70061a8d5586fe29f9a0793e41'
# 原构建导入自身类-2337与本地导出7024同名；只有这一对允许解析，其他重复继续拒绝。
ALIASES=(('ATDGamePlayer',-2337,7024),)
CONDITIONS=((706,1101),(832,1290),(1736,2558))


def _require(condition,code):
    """工作副本、原包和目标函数必须全部匹配；任何失配均停止而非尽力修补。"""
    if not condition:raise ValueError('LEGACY_TOWER_'+code)


def _build(original):
    """生成后复读单个导出和原区，不安装、不启动游戏；冻结摘要由外层再核对。"""
    _require(isinstance(original,bytes) and hashlib.sha256(original).hexdigest()==SOURCE_SHA256,'SOURCE_FINGERPRINT_MISMATCH')
    p=_Package(original,reference_aliases=ALIASES)
    record,rewritten=_modified_function(p,function=FUNCTION,function_sha=FUNCTION_SHA256,
        layout=(2728,1904,1967),conditions=CONDITIONS,pri_class='ATDGamePlayerReplicationInfo',
        super_prefix=bytes.fromhex('1cdcf9ffff003b27000016'))
    at=len(original)-128;result=bytearray(original[:at])+rewritten+original[at:]
    struct.pack_into('<II',result,record['record']+32,len(rewritten),at)
    checked=_Package(bytes(result),reference_aliases=ALIASES)
    _require(checked.export(FUNCTION)[1]==rewritten and checked.refs==p.refs,'ROUNDTRIP_FAILED')
    expected=bytearray(original[:at]);struct.pack_into('<II',expected,record['record']+32,len(rewritten),at)
    _require(result[:at]==expected and result[-128:]==original[-128:],'UNEXPECTED_PACKAGE_CHANGE')
    return bytes(result)


def fix_standalone_legacy_tower_owned(original):
    """只接受固定源并返回固定候选；不能在运行时替换目标包或无摘要安装。"""
    result=_build(original)
    _require(hashlib.sha256(result).hexdigest()==CANDIDATE_SHA256,'CANDIDATE_FINGERPRINT_MISMATCH')
    return result


def install_candidate(guard):
    """只使用外部限时Launcher的持久恢复守卫；默认产品尚未安装此候选。"""
    target=guard.root/RELATIVE
    _require(128<target.stat().st_size<8*1024*1024,'SIZE_INVALID')
    record=guard.apply(RELATIVE,fix_standalone_legacy_tower_owned(target.read_bytes()))
    return dict(profile=PROFILE,source_sha256=SOURCE_SHA256,variant_sha256=record['variant_sha256'],
        native_live_verified=False,requires_bounded_trial=True,online_branch_unchanged=True,asset_changes=False)
