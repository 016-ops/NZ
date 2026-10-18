"""将已鉴权的真实库存投影为当前A006，并在业务提交后应答原生装备；与临时角色探针隔离。"""
import struct
import time
from tools.native_prop_time import prop_time
from tools.email_launcher import LoginError
from adapters.email_protocol import PROTOCOL
from adapters.cn_current.zone import (parse_zone_header, build_research_player_props,
    build_research_player_info, ZONE_PLAYER_PROPS_CODEC_PROFILE, ZONE_EMPTY_ASSET_PLAYER_INFO_PROFILE)
from revival.catalogs import NATIVE_INVENTORY_VERSION, NATIVE_EQUIPMENT_PROJECTION, native_inventory_manifest, native_equipment_available
from revival.native_wallet import WALLET_FIELDS, WALLET_VERSION
from revival.native_pve_benefits import functional


class NativeEquipmentUnsupported(ValueError):
    """已鉴权仓库中的未支持装备操作；允许发送失败响应，不能伪装成功或变更资产。"""
    def __init__(self, result_code):
        """只接受当前TDR的坏GID或类型不匹配错误码，不接收任意响应码。"""
        if result_code not in (0x0108, 0x0109):
            raise ValueError('INVALID_NATIVE_EQUIPMENT_FAILURE_CODE')
        self.result_code = result_code
        super().__init__('UNVERIFIED_NATIVE_AUTOMATIC_LOCATION')


def equipment_failure_response(request, failure):
    """编码当前A009明确失败：保留原请求字段，不生成A00B资产变更，不捕获认证或提交故障。"""
    header = parse_zone_header(request)
    if (not isinstance(failure, NativeEquipmentUnsupported) or header['command'] != 0xA008
            or header['body_length'] != 19 or len(request) != 27):
        raise ValueError('INVALID_NATIVE_EQUIPMENT_FAILURE')
    return struct.pack('>HHHHH', 0x3243, 0xA009, 8, 21, failure.result_code) + request[8:]


class NativeInventoryBridge:
    """绑定单一游戏会话的冻结库存投影；规则不匹配、重复GID或无法表达的实例立即拒绝。"""
    def __init__(self, client, game_token, client_sha256, game_id, *, paged=False):
        """读取本人完整库存；旧模式投影0/2类型，功能模式分页读取并投影全部已定义类型。"""
        self.client, self.game_token, self.game_id = client, game_token, game_id
        self.paged = paged
        self.last_notification = None
        self.last_asset_notifications=[]
        self.last_default_notifications=[]
        self.scope = dict(protocol=PROTOCOL, client_sha256=client_sha256, rules_version=NATIVE_INVENTORY_VERSION)
        self.refresh()

    def refresh(self):
        """核对固定目录中的每个字段、期限和数量；不接受服务端意外变更规则或其他账号快照。"""
        result = self.read_pages() if self.paged else self.client.request('/api/game/inventory/get', self.scope, self.game_token)
        if (result.get('rules_version') != NATIVE_INVENTORY_VERSION or result.get('game_id') != self.game_id
                or not isinstance(result.get('items'), list) or result.get('item_count') != len(result['items'])):
            raise ValueError('INVALID_NATIVE_INVENTORY_SNAPSHOT')
        definitions = {x['item_id']:x for x in native_inventory_manifest()['items']}
        gids = set()
        for row in result['items']:
            definition = definitions.get(row.get('item_id'))
            if (not definition or any(row.get(k) != v for k,v in definition.items())
                    or type(row.get('gid')) is not int or not 1000 < row['gid'] < 2**63 or row['gid'] in gids
                    or type(row.get('quantity')) is not int or not 1<=row['quantity']<=2147483647
                    or (row.get('expires_at') is not None and (type(row['expires_at']) is not int or row['expires_at']<1))
                    or type(row.get('gain_type',48)) is not int or row.get('gain_type',48) not in (35,48)):
                raise ValueError('INVALID_NATIVE_INVENTORY_ITEM')
            gids.add(row['gid'])
        balances = self.validate_wallet(result.get('wallet'))
        self.items = result['items']
        self.balances = balances

    def read_pages(self):
        """有界读取全部库存；同一版本拼接，变化时由上层重新登录，不默默接收混合快照。"""
        from revival.native_features_catalog import FEATURE_VERSION
        request = dict(self.scope,feature_version=FEATURE_VERSION,view='inventory',limit=200,after_gid=1000)
        items=[]
        while True:
            result = self.client.request('/api/game/view',request,self.game_token).get('data')
            if (not isinstance(result,dict) or result.get('game_id')!=self.game_id
                    or result.get('rules_version')!=NATIVE_INVENTORY_VERSION or not isinstance(result.get('items'),list)
                    or len(result['items'])>200 or type(result.get('revision')) is not int
                    or result['revision']!=request.get('revision',result['revision'])):
                raise ValueError('INVALID_NATIVE_PAGE')
            page=result['items']
            if any(type(x.get('gid')) is not int or x['gid']<=request['after_gid'] for x in page):
                raise ValueError('INVALID_NATIVE_PAGE_ORDER')
            items.extend(page)
            after=result.get('next_gid')
            if after is None:
                self.revision=result['revision']
                return dict(result,items=items,item_count=len(items))
            if not page or after!=page[-1]['gid'] or after<=request['after_gid']:
                raise ValueError('INVALID_NATIVE_PAGE_CURSOR')
            request.update(after_gid=after,revision=result['revision'])

    def apply_delta(self, delta):
        """资产提交后更新本人缓存，失败不留半份缓存；不为了单次交易重新下载全仓。"""
        definitions={x['item_id']:x for x in native_inventory_manifest()['items']}
        if not isinstance(delta,dict) or not isinstance(delta.get('items'),list) or not isinstance(delta.get('removed_instance_ids'),list):
            raise ValueError('INVALID_NATIVE_DELTA')
        balances=self.validate_wallet(delta.get('wallet'))
        removed=set(delta['removed_instance_ids'])
        rows={x['gid']:dict(x) for x in self.items if x.get('instance_id') not in removed}
        for row in delta['items']:
            definition=definitions.get(row.get('item_id'))
            if (not definition or type(row.get('gid')) is not int or not 1000<row['gid']<2**63
                    or type(row.get('quantity')) is not int or not 1<=row['quantity']<=2147483647
                    or not isinstance(row.get('instance_id'),str) or not isinstance(row.get('state'),dict)
                    or type(row.get('gain_type',48)) is not int or row.get('gain_type',48) not in (35,48)):
                raise ValueError('INVALID_NATIVE_DELTA_ITEM')
            previous=rows.get(row['gid'])
            if previous and previous.get('instance_id')!=row['instance_id']:
                raise ValueError('NATIVE_GID_REUSED')
            rows[row['gid']]=dict(definition,**row)
        self.items=list(rows.values());self.balances=balances
        self.revision=delta.get('revision')

    def validate_wallet(self, wallet):
        """完整校验七类持久余额；所有字段通过前不修改当前会话的投影。"""
        if (not isinstance(wallet,dict) or wallet.get('rules_version') != WALLET_VERSION
                or not isinstance(wallet.get('balances'),dict) or set(wallet['balances']) != set(WALLET_FIELDS)
                or any(type(value) is not int or not 0 <= value <= WALLET_FIELDS[key]['max']
                       for key,value in wallet['balances'].items())):
            raise ValueError('INVALID_NATIVE_WALLET_SNAPSHOT')
        return dict(wallet['balances'])

    def apply_equipment_state(self, result):
        """使用服务端同事务的完整装备状态更新缓存；不自行推测移出槽或提前确认成功。

        固定道具定义仍来自登录全量校验；轻量状态只能引用这些本人GID及有效父对象。
        任意损坏先拒绝，不能留下半更新的角色、背包或钱包。
        """
        if (not isinstance(result,dict) or result.get('projection') != NATIVE_EQUIPMENT_PROJECTION
                or result.get('rules_version') != NATIVE_INVENTORY_VERSION or result.get('game_id') != self.game_id
                or not isinstance(result.get('equipment'),list) or result.get('equipment_count') != len(result['equipment'])
                or len(result['equipment']) > len(self.items)):
            raise ValueError('INVALID_NATIVE_EQUIPMENT_STATE')
        owned = {x['gid']:x for x in self.items}
        slots, used = {}, set()
        for row in result['equipment']:
            if not isinstance(row,dict) or type(row.get('gid')) is not int or row['gid'] not in owned or row['gid'] in slots:
                raise ValueError('INVALID_NATIVE_EQUIPMENT_STATE')
            item,slot=owned[row['gid']],row.get('equipment_slot')
            parts=slot.split(':') if isinstance(slot,str) else []
            if len(parts)!=3 or parts[0]!='native' or slot in used:
                raise ValueError('INVALID_NATIVE_EQUIPMENT_STATE')
            if parts[1]=='bag':
                valid=item['location']==12 and parts[2]==str(item['item_id'])
            else:
                from revival.native_features_catalog import attachment_compatible, avatar_compatible, avatar_catalog
                from revival.native_avatar_body import compatible as body_compatible, slot as body_slot
                parent=owned.get(int(parts[1]),{}) if parts[1].isdigit() else {}
                body_valid=body_compatible(item,parent) and slot==body_slot(item['item_id'],int(parts[1]))
                valid=body_valid or (parts[2]==str(item['location']) and (
                    (parts[1]=='1' and (item['location'] in (10,11,64) or functional(item))) or
                    (parts[1].isdigit() and int(parts[1]) in owned and (
                     (owned[int(parts[1])]['location']==12 and item['location'] in (0,1,2,3)
                      and str(item['item_id']) not in avatar_catalog()['components'])
                     or attachment_compatible(item,owned[int(parts[1])])
                     or avatar_compatible(item,owned[int(parts[1])])))))
            if not valid:
                raise ValueError('INVALID_NATIVE_EQUIPMENT_STATE')
            slots[row['gid']]=slot;used.add(slot)
        balances=self.validate_wallet(result.get('wallet'))
        self.items=[dict(x,equipment_slot=slots.get(x['gid'])) for x in self.items]
        self.balances=balances

    def login_response(self, server_time):
        """A001使用同一已鉴权钱包的NZ/GP；不能登录时有余额、随后资料通知又将其清零。"""
        from adapters.cn_current.zone import build_clock_login_response, ZONE_CLOCK_COMPAT_PROFILE
        wire=bytearray(build_clock_login_response(server_time=server_time,profile=ZONE_CLOCK_COMPAT_PROFILE))
        struct.pack_into('>ii',wire,26,self.balances['cn2020:TP'],self.balances['cn2020:GP'])
        return bytes(wire)

    def tp_balance_response(self, request):
        """响应本人A50E余额查询；回读持久钱包后发当前A00A TP标志1/TPBALANCE原因42。

        无道具或天赋修改标志，嵌套计数全部0；空天赋版本3由当前DLL规范化证据确定。
        拒绝其他Uin、截断和错误命令，既不充值也不把查询目标当作身份来源。
        """
        header=parse_zone_header(request)
        if header['command']!=0xA50E or header['body_length']!=8 or struct.unpack_from('>Q',request,8)[0]!=self.game_id:
            raise ValueError('INVALID_NATIVE_TP_BALANCE_REQUEST')
        self.apply_equipment_state(self.client.request('/api/game/inventory/get',
            dict(self.scope,projection=NATIVE_EQUIPMENT_PROJECTION),self.game_token))
        body=bytearray(62)
        struct.pack_into('>iHi',body,0,1,42,self.balances['cn2020:TP'])
        body[40]=3
        return struct.pack('>HHHH',0x3243,0xA00A,8,len(body))+body

    def packets(self):
        """5件一包，只有最终包置末包位；永久0/2类型沿当前消费者可用时间-1分支。

        未装备OwnerGID=0、Location=-1；根1只用于已装备角色/背包，武器引用实际背包GID。
        不把隐藏组件或未验证的限时计算字段伪造成已可用武器。
        """
        props = []
        from revival.native_avatar_body import wire_binding
        owned={row['gid']:row for row in self.items}
        for row in self.items:
            if row.get('expires_at') is not None and row['expires_at']<=int(time.time()):
                continue
            if not self.paged and row['consume_type'] not in (0, 2):
                continue
            owner,location=wire_binding(row,owned)
            # -1也用于全能体验卡的根1；只有实际角色父GID才读取父物品期限。
            if location==-1 and owner not in (0,1) and owned[owner].get('expires_at') is not None and owned[owner]['expires_at']<=int(time.time()):
                continue  # 角色过期后保留其资产与父关系，不向客户端发送悬空的身体资源。
            expires=row.get('expires_at')
            props.append(dict(Uin=self.game_id, GID=row['gid'], ItemId=row['item_id'], ResFlag=0,
                StackNum=row['quantity'], Durability=row.get('state',{}).get('durability',row['durability_max']),
                DurabilityMax=row['durability_max'], OwnerGID=owner, Location=location,
                **(prop_time(expires,consume_type=row['consume_type']) if self.paged else dict(ObtainTime=0,Validity=0,AvailbilityHour=0)), UseHour=0, GainType=row.get('gain_type',48)))
        # 先发送父对象和角色，再发送背包子装备；最后才发其余未装备库存。
        props.sort(key=lambda p:(0 if p['Location'] in (10,11,12,64) else 1 if p['Location'] >= 0 else 2, p['GID']))
        return [build_research_player_props(game_id=self.game_id, props=props[i:i+5], is_last=i+5>=len(props),
                profile=ZONE_PLAYER_PROPS_CODEC_PROFILE) for i in range(0, max(1,len(props)), 5)]

    def player_info(self, nickname):
        """从同一快照引用真实角色、钱包和已持久百宝箱成长；未接入的其他统计保持零。"""
        wire = bytearray(build_research_player_info(game_id=self.game_id, nickname_bytes=nickname,
                                                   profile=ZONE_EMPTY_ASSET_PLAYER_INFO_PROFILE))
        # 当前兼容大厅以PvE角色优先；保留两阵营独立槽，PvE角色缺省时才用保卫者。
        role = next((x for x in self.items if x.get('equipment_slot') == 'native:1:64'), None)
        role = role or next((x for x in self.items if x.get('equipment_slot') == 'native:1:10'), None)
        if role:
            struct.pack_into('>Q', wire, 8+18+len(nickname)+1+50, role['gid'])
        if self.paged:
            # 当前PlayerInfo.ClanID主机偏移120，昵称动态压缩后的固定段偏移66。
            struct.pack_into('>Q',wire,8+18+len(nickname)+1+66,getattr(self,'native_clan_id',0))
        for key,spec in WALLET_FIELDS.items():
            struct.pack_into('>'+spec['format'],wire,8+18+len(nickname)+1+spec['offset'],self.balances[key])
        if self.paged:
            # 当前TDR的DepositExpansionCapacity为有符号32位；兼容窗口提供一百万扩展槽。
            # 服务端不设总槽限；客户端实际硬边界另行实测，不将这个字段描述为无限内存。
            struct.pack_into('>i',wire,8+18+len(nickname)+1+334,1000000)
            from revival.native_pve_benefits import vip_info
            vip=vip_info(self.items,int(time.time()))
            # PlayerInfo主机偏移328起的四个VIP字段；固定段从主机偏移44开始。
            struct.pack_into('>iiii',wire,8+18+len(nickname)+1+284,*vip.values())
            from revival.native_kun import snapshot as kun_snapshot
            # 当前PlayerInfo.KunTaoEvolutionLevel主机偏移352，固定段减44为308，类型为uint16。
            # 按真实有效核心种类计算；没有枪或核心不伪造验收满级。
            struct.pack_into('>H',wire,8+18+len(nickname)+1+308,kun_snapshot(self.items,int(time.time()))['level'])
        return bytes(wire)

    def equip(self, request):
        """解析严格19B A008、向本人业务接口提交并核对回读；失败不会发A009成功。

        当前仓库点击装备会发送Location=-1，表示由服务端选择槽位；只能由冻结目录
        的本人基础武器、两阵营/PvE角色、背包或明确默认匕首解析，不能扩展其他隐藏物品或所有者权限。
        未支持物品以专用异常交给已校验A009失败应答；认证、提交或回读损坏仍不得伪造成功。
        """
        started = time.perf_counter()
        self.last_equip_timing = None
        header = parse_zone_header(request)
        if header['command'] != 0xA008 or header['body_length'] != 19:
            raise ValueError('INVALID_NATIVE_EQUIPMENT_REQUEST')
        operation, subject, target, location = struct.unpack('>hQQb', request[8:])
        self.last_notification = None
        self.last_asset_notifications=[]
        self.last_default_notifications=[]
        if operation == 0:
            item = next((x for x in self.items if x['gid'] == subject), None)
            if item is None:
                raise NativeEquipmentUnsupported(0x0108)  # 当前TDR：ZONE_FAIL_BADPROPGID。
            if not native_equipment_available(item):
                raise NativeEquipmentUnsupported(0x0109)  # 当前TDR：ZONE_FAIL_PROPTYPEMISMATCH。
            from revival.native_features_catalog import avatar_catalog, avatar_compatible
            if str(item['item_id']) in avatar_catalog()['components']:
                parent=next((x for x in self.items if x['gid']==target),{})
                if not avatar_compatible(item,parent):
                    raise NativeEquipmentUnsupported(0x0109)
            if item['location']==94:
                from revival.native_features_catalog import attachment_compatible
                parent=next((x for x in self.items if x['gid']==target),{})
                if not attachment_compatible(item,parent):
                    raise NativeEquipmentUnsupported(0x0109)
            if item['location']==-1 and not functional(item):
                from revival.native_avatar_body import compatible as body_compatible
                parent=next((x for x in self.items if x['gid']==target),{})
                if not body_compatible(item,parent):
                    raise NativeEquipmentUnsupported(0x0109)
        if operation == 0 and location == -1:
            location = item['location']
        fields = dict(operation=operation, subject=subject, target=target, location=location)
        try:
            result = self.client.request('/api/game/inventory/equip',
                dict(self.scope, projection=NATIVE_EQUIPMENT_PROJECTION, **fields), self.game_token)
        except LoginError as error:
            # 空背包/过期/错父对象属于可恢复的操作拒绝，A009失败后继续处理当前会话。
            # 鉴权失败、未知提交结果和损坏回读仍向上抛出，绝不伪造成功。
            if str(error) in ('NATIVE_EQUIPMENT','NATIVE_EQUIPMENT_CONFLICT'):
                raise NativeEquipmentUnsupported(0x0109) from error
            raise
        committed = time.perf_counter()
        commit_http = getattr(self.client, 'last_request_timing', None)
        if (result.get('status') not in ('SAVED', 'UNCHANGED') or result.get('rules_version') != NATIVE_INVENTORY_VERSION
                or any(result.get(k) != v for k,v in fields.items())):
            raise ValueError('UNCONFIRMED_NATIVE_EQUIPMENT_COMMIT')
        self.last_commit_status = result['status']
        if result.get('inventory_delta'):
            if getattr(self,'features',None):
                self.last_asset_notifications=self.features.apply_updates(result['inventory_delta'],2)
            else:
                self.apply_delta(result['inventory_delta'])
        self.apply_equipment_state(result.get('equipment_state'))
        for restored in result.get('default_equipment',[]):
            row=next((x for x in self.items if x['gid']==restored['gid']),None)
            if not row or row['item_id']!=100013 or row['equipment_slot']!=f"native:{restored['target']}:2":
                raise ValueError('INVALID_DEFAULT_KNIFE_READBACK')
            self.last_default_notifications.append(struct.pack('>HHHHhQQb',0x3243,0xa00b,8,19,0,row['gid'],restored['target'],2))
        for restored in result.get('avatar_equipment',[]):
            from revival.native_features_catalog import avatar_compatible
            from revival.native_avatar_body import compatible as body_compatible, slot as body_slot
            row=next((x for x in self.items if x['gid']==restored['gid']),None)
            parent=next((x for x in self.items if x['gid']==restored['target']),None)
            body=bool(row and parent and body_compatible(row,parent))
            expected=body_slot(row['item_id'],restored['target']) if body else f"native:{restored['target']}:{restored['location']}"
            if (not row or not parent or not (body or avatar_compatible(row,parent))
                    or row['equipment_slot']!=expected):
                raise ValueError('INVALID_AVATAR_COMPONENT_READBACK')
            self.last_default_notifications.append(struct.pack('>HHHHhQQb',0x3243,0xa00b,8,19,0,row['gid'],restored['target'],restored['location']))
        refreshed = time.perf_counter()
        item = next((x for x in self.items if x['gid'] == subject), None)
        expected_slot = f'native:{target}:{location}' if operation == 0 else None
        if operation == 0 and location == -1 and item is not None and not functional(item):
            from revival.native_avatar_body import slot as body_slot
            expected_slot=body_slot(item['item_id'],target)
        if operation == 0 and location == 12 and item is not None:
            # 背包的根挂接按物品定义独立持久化；读回完整装备视图后才能确认。
            expected_slot = f"native:bag:{item['item_id']}"
        if item is None or item.get('equipment_slot') != expected_slot:
            raise ValueError('NATIVE_EQUIPMENT_READBACK_MISMATCH')
        effective = struct.pack('>hQQb', operation, subject, target, location)
        # A009完成请求，A00B更新客户端本地资产树；两者只能在同一持久提交核对后生成。
        self.last_notification = struct.pack('>HHHH',0x3243,0xA00B,8,19) + effective
        body = struct.pack('>H', 0x8100) + effective
        self.last_equip_timing = dict(commit_ms=round((committed-started)*1000,3),
            refresh_ms=round((refreshed-committed)*1000,3),
            total_ms=round((time.perf_counter()-started)*1000,3),
            commit_http=commit_http if isinstance(commit_http,dict) else None,
            refresh_http=None, snapshot_mode=NATIVE_EQUIPMENT_PROJECTION)
        return struct.pack('>HHHH', 0x3243, 0xA009, 8, len(body)) + body
