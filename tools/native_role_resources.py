"""当前两组角色资源的临时加载探针；固定兼容夹具不写真实库存，也不能用于正式资产投影。"""
import struct
from tools.client_workspace import checked_path, digest
from adapters.cn_current.zone import build_research_player_props, ZONE_PLAYER_PROPS_CODEC_PROFILE, parse_zone_header
from adapters.cn_current.zone import build_research_player_info, ZONE_EMPTY_ASSET_PLAYER_INFO_PROFILE

ROLE_RESOURCE_PROBE_PROFILE = 'cn2020-role-resource-smoke-research-v1'
ROLE_EQUIP_PROBE_PROFILE = 'cn2020-role-equip-smoke-research-v1'
ROLE_INFO_REFRESH_PROFILE = 'cn2020-role-info-refresh-smoke-research-v1'


def build_role_resource_probe(root, game_id):
    """固定当前物品/家族表后生成两件临时角色，供当前自有客户端实际尝试依赖加载。

    100014/100016来自已核对的当前配置；GID1001/1002是本探针局部值，不是玩家业务实例。
    OwnerGID=1来自当前153DA46/153DA7B写入和153E01C/153E02A角色自修复筛选；
    当前两项ConsumeType=0走153DD42..153DD4B可用时间-1分支。其他标量是明确兼容选择。
    角色索引和RoleType仍不构造，资源是否可用必须由本次游戏实际显示/日志验收。
    """
    expected = {
        'TGame/Config/DefaultItemLibrary.ini': '741963c2174004735a9456ea559a6976f53665f591f9ba995d7889b8db0911a6',
        'TGame/Config/DefaultTGResManager.ini': '9361eb84e14f60e9a6275f7885f3e1da5d0db1c626bd6498ca39bb5557354605',
    }
    for relative, sha256 in expected.items():
        if digest(checked_path(root, relative)) != sha256:
            raise ValueError('CURRENT_ROLE_RESOURCE_CONFIG_MISMATCH')
    props = []
    for index, item_id in enumerate((100014, 100016)):
        props.append(dict(Uin=game_id, GID=1001+index, ItemId=item_id, ResFlag=0,
                          StackNum=1, ObtainTime=0, Validity=0, Durability=0,
                          DurabilityMax=0, OwnerGID=1, Location=10+index,
                          AvailbilityHour=0, UseHour=0, GainType=48))
    return build_research_player_props(game_id=game_id, props=props, is_last=True,
                                       profile=ZONE_PLAYER_PROPS_CODEC_PROFILE)


def build_role_equip_probe_response(request, *, profile):
    """仅回复本Job已发送的1001角色装备到根1/位置10的固定A008，不实现真实库存装备接口。

    boot44/45实际请求为这19B；当前A009在153D292置8100、153D2AF比较后调用15C7720，
    并执行原生装备委托。因此不另发未知A00B或修改就绪位。重复相同请求得到相同字节。
    任意其他实例、操作、父GID或位置拒绝，不能用临时夹具替代资产账本的实际拥有校验。
    """
    if profile != ROLE_EQUIP_PROBE_PROFILE or type(profile) is not str:
        raise ValueError('UNSUPPORTED_ROLE_EQUIP_PROBE_PROFILE')
    header = parse_zone_header(request)
    expected = struct.pack('>hQQb', 0, 1001, 1, 10)
    if header['command'] != 0xA008 or request[8:] != expected:
        raise ValueError('ROLE_EQUIP_REQUEST_OUTSIDE_PROBE')
    body = struct.pack('>H', 0x8100) + expected
    return struct.pack('>HHHH', 0x3243, 0xA009, 8, len(body)) + body


def build_role_info_refresh(game_id, nickname_bytes, *, profile):
    """在临时道具/装备之后重发A005引用已存在的1001角色，验证当前资料与道具的初始化顺序。

    当前TDR PlayerInfo的CurRoleGID host偏移94，昵称host结束44；wire在昵称后移动50B。
    18B前缀含外层Result、Uin、VerifyKey及字符串长度。固定当前DLL非零GID夹具独立互证。
    这里只改生成的包中这8B，不改原文件、真实资产、RoleType或16位角色选择索引。
    """
    if type(profile) is not str or profile != ROLE_INFO_REFRESH_PROFILE:
        raise ValueError('UNSUPPORTED_ROLE_INFO_REFRESH_PROFILE')
    wire = bytearray(build_research_player_info(game_id=game_id, nickname_bytes=nickname_bytes,
                                               profile=ZONE_EMPTY_ASSET_PLAYER_INFO_PROFILE))
    offset = 8 + 18 + len(nickname_bytes) + 1 + (94 - 44)
    if wire[offset:offset + 8] != bytes(8):
        raise ValueError('CURRENT_PLAYER_INFO_ROLE_FIELD_CHANGED')
    struct.pack_into('>Q', wire, offset, 1001)
    return bytes(wire)
