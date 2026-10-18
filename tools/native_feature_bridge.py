"""当前客户端功能消息适配；鉴权失败断开，业务失败使用专用应答，未知提交先查原操作。"""
import hashlib
import json
import math
from pathlib import Path
import time
import uuid
from datetime import datetime, timedelta, timezone
import calendar

from adapters.cn_current.features import metadata, packet, parse
from revival.native_features_catalog import FEATURE_VERSION, FRAGMENTS
from revival.native_wallet import WALLET_VERSION
from tools.email_launcher import LoginError
from tools.native_clan_bridge import NativeClanMessages, CLAN_COMMANDS
from tools.native_workshop_bridge import NativeWorkshopMessages
from tools.native_engineering_bridge import NativeEngineeringMessages, ENGINEERING_COMMANDS
from tools.native_prop_time import prop_time
from tools.native_room_bridge import NativeRoomMessages,ROOM_COMMANDS,ROOM_ERRORS
from revival.native_features_catalog import ROOM_VERSION

# 当前TDR常量经过摘要校验；各应答必须按实际消费分支选成功码。
# FA17当前exe RVA1509280/1509290明确比较ZONE_ERR_SUCC；不使用同族开奖成功码。
CONSTANTS = metadata()['constants']
BUSINESS_ERRORS = {'INVALID','LIMIT','QUANTITY_LIMIT','BALANCE','INVENTORY','ITEM_ACTION','ITEM_IN_USE','MATERIALS',
    'OFFER','GIFT','RENEW','PERMANENT','QUOTE_CHANGED','RULE_CONFLICT','ACTIVITY_DISABLED','ACTIVITY_CLOSED',
    'POOL_EMPTY','EXCLUSIVE','SOCIAL_STATE','SOCIAL_DENIED','CLAN_STATE','CLAN_NAME','CLAN_PERMISSION','CLAN_OWNER',
    'NOT_FOUND','UNVERIFIED_RESOURCE','PENDING_DRAW','PVE_STORE_LEVEL','PVE_STORE_PROP','PVE_STORE_TAKEN'} | ROOM_ERRORS


class NativeFeatureBridge(NativeClanMessages, NativeWorkshopMessages, NativeEngineeringMessages,NativeRoomMessages):
    """单游戏会话功能路由；不持久化令牌，日志只写无凭据的原请求及幂等键。"""
    COMMANDS = {0x37a2,0x3932,0xa167,0xa363,0xa375,0xa505,0xa509,0xa50b,0x1112,0x1114,0xfa16,0xfa0a,0xfa0c,0xfa0e,
                0xfa05,0xfa18,0xfa1a,0xfa1c,0xfa1e,0xfa20,0xfa23,
                0xc106,0xc107,0xc108,0xc110,0xc117,0xc119,
                0x3601,0x3603,0xa303,0xa305,0xa307,0xa309,0xa30b,0xa30d,0xa30f,0xa311,0xa31f,0xa321,0xa322,0xa329,0xa405} | CLAN_COMMANDS | ENGINEERING_COMMANDS | ROOM_COMMANDS

    def __init__(self, inventory, journal):
        """绑定已分页校验的本人库存及账号专用重试日志；已有未决操作必须先查回执。"""
        self.inventory=inventory
        self.client=inventory.client
        self.scope=dict(inventory.scope,feature_version=FEATURE_VERSION)
        self.journal=Path(journal)
        self.journal.parent.mkdir(parents=True,exist_ok=True)
        self.pending=None
        if self.journal.exists():
            saved=json.loads(self.journal.read_text(encoding='utf-8'))
            if saved.get('version')=='native-command-dpapi-v1':
                # 原生短房间口令也不明文落盘；跨用户/损坏的DPAPI记录不能退化成可执行请求。
                import base64
                from tools.native_launcher_window import protect
                if set(saved)!={'version','protected'}:raise ValueError('INVALID_OPERATION_JOURNAL')
                saved=json.loads(protect(base64.b64decode(saved['protected'],validate=True),decrypt=True))
            if saved.get('game_id')!=inventory.game_id or not isinstance(saved.get('request'),dict):
                raise ValueError('INVALID_OPERATION_JOURNAL')
            self.pending=saved
        self.last=None
        self.event_ids=[]
        self.last_friend_state=None
        self.last_room=None
        self.presented_draws={}  # 同连接已发送的开奖回执；自动奖池刷新表示客户端收悉，并非玩家点确认。
        self.draw_update_until=0  # 原生开奖动画期间暂缓资产通知，避免仓库重绘销毁开奖弹层。
        self.workshop_receipt=None  # 写完本批结果帧后确认传输；不是玩家已读标志。
        self.next_poll=0

    def view(self, view, **values):
        """所有读取都使用游戏凭据和本人范围，对象信封避免裸数组响应误判。"""
        reply=self.client.request('/api/game/view',dict(self.scope,view=view,**values),self.inventory.game_token)
        if 'data' not in reply:
            raise ValueError('INVALID_FEATURE_VIEW')
        return reply['data']

    def save_pending(self, value):
        """请求发出前先原子替换本账号日志，崩溃恢复重用原键；文件内绝不写游戏令牌。"""
        temporary=self.journal.with_suffix('.tmp')
        stored=value
        if value.get('request',{}).get('password'):
            import base64
            from tools.native_launcher_window import protect
            stored=dict(version='native-command-dpapi-v1',protected=base64.b64encode(
                protect(json.dumps(value,ensure_ascii=True).encode())).decode('ascii'))
        temporary.write_text(json.dumps(stored,ensure_ascii=True),encoding='utf-8')
        temporary.replace(self.journal)
        self.pending=value

    def clear_pending(self):
        """只删除本对象的单个已确认日志；从不递归清理运行目录或历史证据。"""
        self.journal.unlink(missing_ok=True)
        self.pending=None

    def commit(self, action, **values):
        """提交前落盘键，未知结果先查询；短时间相同按钮重放原结果，避免双击重复消费。"""
        payload=dict(self.scope,action=action,reason='native client '+action,**values)
        fingerprint=hashlib.sha256(json.dumps(payload,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        if self.last and self.last[0]==fingerprint and time.monotonic()-self.last[1]<2:
            return self.last[2]
        if self.pending:
            recovered=self.view('operation',idempotency_key=self.pending['request']['idempotency_key'])
            if recovered['status']=='COMMITTED':
                old=self.pending; self.clear_pending()
                if old['fingerprint']==fingerprint:
                    return recovered['result']
            elif self.pending['fingerprint']!=fingerprint:
                raise LoginError('PENDING_OPERATION_REQUIRES_RETRY')
        if not self.pending:
            key=uuid.uuid4().hex
            self.save_pending(dict(game_id=self.inventory.game_id,fingerprint=fingerprint,
                request=dict(payload,request_id=key,idempotency_key=key)))
        try:
            result=self.client.request('/api/game/command',self.pending['request'],self.inventory.game_token)
        except LoginError as error:
            if str(error) in BUSINESS_ERRORS:
                self.clear_pending()  # 明确业务拒绝没有提交，允许用户修正输入。
            raise
        if result.get('feature_version')!=FEATURE_VERSION or result.get('game_id')!=self.inventory.game_id:
            raise ValueError('INVALID_FEATURE_COMMIT')
        self.clear_pending()
        self.last=(fingerprint,time.monotonic(),result)
        return result

    def item(self, gid, resource=None):
        """桥接缓存只作参数一致性检查，最终归属和期限仍由业务事务重新检查。"""
        row=next((r for r in self.inventory.items if r['gid']==gid),None)
        if row is None or (resource is not None and row['item_id']!=resource):
            raise LoginError('INVENTORY')
        return row

    def prop(self, row, *, deleted=False):
        """把已提交实例映射到当前PropInfo；有限期按兼容小时表达，服务端精确秒数仍权威。"""
        from revival.native_avatar_body import wire_binding
        owner,location=wire_binding(row,{r['gid']:r for r in self.inventory.items})
        expires=row.get('expires_at')
        return dict(Uin=self.inventory.game_id,GID=row['gid'],ItemId=row['item_id'],ResFlag=0,
            StackNum=0 if deleted else row['quantity'],**prop_time(expires,consume_type=row['consume_type']),
            Durability=row.get('state',{}).get('durability',row['durability_max']),DurabilityMax=row['durability_max'],
            OwnerGID=owner,Location=location,UseHour=0,GainType=row.get('gain_type',48))

    def updates(self, result, reason):
        """从本次回执生成最多100项的A00A增量；先验证全部缓存，再组包，绝不重发整仓A006。"""
        delta=result.get('delta')
        if not delta or delta.get('revision',0)<=getattr(self.inventory,'revision',-1):
            return []
        # 回执前可能还有别人赠送或后台发放，必须先补连续版本，不能直接跳到本次订单版本。
        return self.synchronize(reason)

    def synchronize(self, reason=2):
        """心跳和业务提交都补取本人变化；通常只有数项，不重新下载4323项库存。"""
        if time.monotonic()<self.draw_update_until:
            return []  # 奖励已在数据库提交；确认弹层后的查询再补增量，异常关闭最多延迟60秒。
        packets=[]
        for _ in range(100):
            result=self.view('inventory.changes',after_revision=self.inventory.revision)
            if result.get('reload'):
                before={r['instance_id']:r for r in self.inventory.items}
                self.inventory.refresh()
                # 迁移前的历史空洞只重建缓存，再用A00A差量；仍不重发A006触发全仓整理。
                current={r['instance_id']:r for r in self.inventory.items}
                delta=dict(items=[r for i,r in current.items() if before.get(i)!=r],
                    removed_instance_ids=list(set(before)-set(current)),revision=self.inventory.revision,
                    wallet={'rules_version':WALLET_VERSION,
                            'balances':self.inventory.balances})
                self.inventory.items=list(before.values());self.inventory.revision=-1
                packets.extend(self.apply_updates(delta,reason))
                return packets
            if result.get('delta'):
                packets.extend(self.apply_updates(result['delta'],reason))
            if not result.get('more'):
                return packets
        raise ValueError('SYNC_BACKLOG_REQUIRES_RECONNECT')

    def apply_updates(self, delta, reason):
        """应用已完整覆盖的版本片段；同一旧回执不覆盖较新本地状态。"""
        if delta.get('revision',0)<=getattr(self.inventory,'revision',-1):
            return []  # 重放旧订单只重回应答，不把较新的缓存覆盖回旧资产版本。
        before={r['instance_id']:r for r in self.inventory.items if 'instance_id' in r}
        previous_wallet=dict(self.inventory.balances)
        self.inventory.apply_delta(delta)
        after={r['instance_id']:r for r in self.inventory.items}
        changes=[dict(OpType=2,Prop=self.prop(before[i],deleted=True)) for i in delta['removed_instance_ids'] if i in before]
        for row in delta['items']:
            current=after[row['instance_id']]
            previous=before.get(row['instance_id'])
            # 原生MOD不负责计数更新，材料扣除/增加必须走专门的MOD_STACKNUM分支。
            if previous and previous['quantity']!=current['quantity']:
                changes.append(dict(OpType=CONSTANTS['EPropsOperationType_MOD_STACKNUM'],Prop=self.prop(current)))
            if not previous or any(previous.get(k)!=current.get(k) for k in ('expires_at','state','equipment_slot','gain_type')):
                changes.append(dict(OpType=3 if previous else 1,Prop=self.prop(current)))
        wallet=self.inventory.balances
        values=dict(UpdateFlag=1|2|16|256|1024|4096|(8 if changes else 0),Reason=reason,
            TGamePoint=wallet['cn2020:TP'],GoldPoint=wallet['cn2020:GP'],MonthPoint=wallet['cn2020:MP'],
            HappyPoint=wallet['cn2020:HP'],EvolutionPoint=wallet['cn2020:EP'],
            StarPoint=wallet['cn2020:SP'],TalentSkills={'Version':3})
        replies=[packet(0xa00a,dict(values,Props=changes[i:i+100])) for i in range(0,max(1,len(changes)),100)]
        # 当前客户端仅在Reason42读取NZ余额，普通资产原因即使带TP标志也忽略。
        # 钱包单独通知不能替换材料/制造原因，否则其界面回调收不到对应变化。
        for code,flag,cause,field in [('cn2020:TP',1,'EUpdatePropertyType_TPBALANCE','TGamePoint'),
                                     ('cn2020:HP',256,'EUpdatePropertyType_CHANGE_HAPPYPOINT','HappyPoint')]:
            if previous_wallet[code]!=wallet[code]:
                replies.append(packet(0xa00a,dict(UpdateFlag=flag,Reason=CONSTANTS[cause],
                    **{field:wallet[code]},TalentSkills={'Version':3})))
        return replies

    def skin_snapshot(self):
        """重登回放已保存皮肤；超过协议一次容量时明确拒绝，不能静默漏掉玩家状态。"""
        rows=[]
        for item in self.inventory.items:
            skin=item.get('state',{}).get('skin')
            if skin:
                rows.append(dict(WeaponGID=item['gid'],SkinPropGID=skin['gid'],SkinResId=skin['item_id']))
        if len(rows)>320:
            raise ValueError('SKIN_PACKET_CAPACITY')
        return packet(0x37a1,dict(EquippedSkinInfo={'SkinDataList':rows[:64]},EquippedSkinInfoEx={'SkinDataList':rows[64:]}))

    def swallow_snapshot(self):
        """按稳定GID回放可逆的一对一融合；当前消息容量8在业务提交前已验证。"""
        rows=[]
        for item in self.inventory.items:
            relation=item.get('state',{}).get('swallow')
            if relation:
                self.item(relation['gid'],relation['item_id'])
                rows.append(dict(ItemId=item['item_id'],ItemGid=item['gid'],
                    SwollowItemId=relation['item_id'],SwollowItemGid=relation['gid']))
        return packet(0x3931,dict(PlayerKunTaoPetInfo=dict(Uin=self.inventory.game_id,KunTaoPetInfoList=rows)))

    def handle(self, raw):
        """仅接已列入验收夹具的命令；解析损坏与身份失败不被普通业务失败处理掩盖。"""
        command,fields=parse(raw)
        if command not in self.COMMANDS:
            raise ValueError('UNBOUND_FEATURE_COMMAND')
        try:
            return self.dispatch(command,fields)
        except LoginError as error:
            if str(error) not in BUSINESS_ERRORS:
                raise
            if command in ROOM_COMMANDS:return self.room_failure(command,fields,str(error))
            return [self.failure(command,fields,str(error))]

    def failure(self, command, fields, code):
        """使用消息所属错误域并回显目标，失败没有资产通知，调用方可继续处理下一请求。"""
        if command in CLAN_COMMANDS:
            return self.clan_failure(command,fields)
        if command in ENGINEERING_COMMANDS:
            return self.engineering_failure(command,fields,code)
        if command in (0x3601,0x3603):
            value=CONSTANTS[{'PVE_STORE_LEVEL':'ZONE_VIP_LEVEL_POINT_ERROR','PVE_STORE_TAKEN':'ZONE_VIP_PROP_HAD_TAKEN'}.get(code,'ZONE_VIP_LEVEL_PROP_ERROR')]
            return packet(command+1,dict(Seq=fields['Seq'],Result=value) if command==0x3603 else dict(result=value))
        if command==0xa505:
            result={'BALANCE':524,'GIFT':516,'QUOTE_CHANGED':537,'OFFER':514}.get(code,512)
            return packet(0xa506,dict(Result=result,BuyType=fields['BuyType'],PayType=fields['PayType']))
        if command in (0xa509,0xa50b):
            values=dict(Result=524 if code=='BALANCE' else 512)
            if command==0xa509:
                values.update(PropGid=fields['PropGid'],CommodityId=fields['CommodityId'],PayType=fields['PayType'])
            return packet(command+1,values)
        if command==0xa363:
            return packet(0xa364,dict(Result=CONSTANTS['ZONE_FAIL_PROPCANNOTDROP'],PropGid=fields['PropGid']))
        if command==0xa375:
            result=CONSTANTS['EXCHANGE_ERR_PROPNOTENOUGH'] if code=='MATERIALS' else CONSTANTS['EXCHANGE_ERR_FAIL']
            return packet(0xa376,dict(Result=result,FortuneKeyType=fields['FortuneKeyType'],FortuneKeyCount=fields['FortuneKeyCount']))
        if command in (0x1112,0x1114):
            return packet(0x1113 if command==0x1112 else 0x1115,dict(Uin=self.inventory.game_id,Type=fields['Type'],Date=fields['Date'],**({'Result':1} if command==0x1112 else {'Awarded':0})))
        if command in (0xc106,0xc107,0xc108,0xc110,0xc117,0xc119):
            return packet(0xc109,dict(Result=2577,ID=fields.get('ID',90001601),Stage=fields.get('Stage',1)))
        if command==0x37a2:
            return packet(0x37a3,{'Result':CONSTANTS['WEAPON_SKIN_FAIL_UNKOWN']})
        if command==0x3932:
            return packet(0x3934,dict(fields,Result=CONSTANTS['KUNTAO_PET_SWOLLOW_FAIL']))
        if command==0xa167:
            return packet(0xa168,dict(fields,Result=264))
        if command in (0xfa0a,0xfa0c,0xfa0e,0xfa16):
            result=CONSTANTS['LOTTERY_ERR_EXCHANGE_NOTENOUGH'] if code=='MATERIALS' else CONSTANTS['LOTTERY_ERR_FAIL']
            values={'Result':result}
            if command!=0xfa16:
                values['FragmentId']=fields['FragmentId']
            return packet(command+1,values)
        if command in (0xfa18,0xfa1a,0xfa1c,0xfa1e):
            return packet(command+1,dict(Result=CONSTANTS['LOTTERY_ERR_FAIL']))
        if command==0xfa05:
            return packet(0xfa06,dict(ExchangeResult=CONSTANTS['LOTTERY_ERR_FAIL'],Index=fields['Index']))
        if command in (0xfa20,0xfa23):
            return packet(command+1,dict(Result=CONSTANTS['LOTTERY_ERR_EXCHANGE_NOTENOUGH'] if code=='MATERIALS' else CONSTANTS['LOTTERY_ERR_FAIL']))
        if command==0xa405:
            return packet(0xa402,dict(ChatType=fields['C2ZNP2PType'],RecipientUin=fields['RecipientUin'],ErrorInfo=CONSTANTS['SNS_ERR_FAIL']))
        responses={0xa303:0xa304,0xa305:0xa308,0xa307:0xa308,0xa309:0xa30a,0xa30b:0xa30c,0xa30d:0xa30e,
                   0xa30f:0xa310,0xa311:0xa312,0xa31f:0xa320,0xa321:0xa320,0xa322:0xa320,0xa329:0xa32a}
        target=responses[command]
        values={'Result':CONSTANTS['SNS_ERR_FAIL']}
        if command==0xa303:
            values={'Type':fields['Type']}
        return packet(target,values)

    def dispatch(self, command, fields):
        """各入口只构造领域命令；数量、价格、奖池及归属由服务端确定。"""
        if 'Uin' in fields and fields['Uin']!=self.inventory.game_id:
            raise ValueError('FEATURE_IDENTITY_MISMATCH')
        if command in ROOM_COMMANDS:return self.room_handle(command,fields)
        if command==0x3601:
            # 原生查询只回读本人持久成长与期限；不能借查询升级或发奖。
            return [packet(0x3602,dict(result=CONSTANTS['ZONE_ERR_SUCC'],VipBaseInfo=self.view('pve.benefits')['vip_info']))]
        if command==0x3603:
            # 此消息的Seq是10014等权益编号，并非可忽略的流水号；真实奖励ID仍须服务端核对。
            result=self.commit('pve.store.claim',benefit_id=fields['Seq'],prop_ids=fields['PropIds'])
            return [*self.updates(result,2),packet(0x3604,dict(Seq=fields['Seq'],Result=CONSTANTS['ZONE_ERR_SUCC']))]
        if command in CLAN_COMMANDS:
            return self.clan_handle(command,fields)
        if command in ENGINEERING_COMMANDS:
            return self.engineering_handle(command,fields)
        if command==0x37a2:
            self.item(fields['WeaponGID'],fields['WeaponResID'])
            if fields['SkinGID']:
                self.item(fields['SkinGID'],fields['SkinResId'])
            result=self.commit('inventory.skin',gid=fields['WeaponGID'],skin_gid=fields['SkinGID'] or None)
            updates=self.updates(result,33)
            skin=dict(WeaponGID=fields['WeaponGID'],SkinPropGID=fields['SkinGID'],SkinResId=fields['SkinResId'] if fields['SkinGID'] else 0)
            return [packet(0x37a3,dict(Result=CONSTANTS['ZONE_ERR_SUCC'],SkinData=skin)),*updates,self.skin_snapshot()]
        if command==0x3932:
            self.item(fields['ItemGid'],fields['ItemId'])
            self.item(fields['SwollowItemGid'],fields['SwollowItemId'])
            action={CONSTANTS['KunTaoPetOperatorType_Equip']:'inventory.swallow',
                    CONSTANTS['KunTaoPetOperatorType_TakeOff']:'inventory.unswallow'}.get(fields['OperatorType'])
            if not action:
                raise LoginError('ITEM_ACTION')
            result=self.commit(action,gid=fields['ItemGid'],material_gid=fields['SwollowItemGid'])
            updates=self.updates(result,98)
            return [packet(0x3934,dict(fields,Result=CONSTANTS['KUNTAO_PET_SWOLLOW_SUCCESS'])),*updates,self.swallow_snapshot()]
        if command==0xa167:
            self.item(fields['PropGid'],fields['ItemId'])
            result=self.commit('inventory.delete',gid=fields['PropGid'])
            return [packet(0xa168,dict(fields,Result=CONSTANTS['ZONE_ERR_SUCC'])),*self.updates(result,79)]
        if command==0xa505:
            return self.purchase(fields)
        if command==0xa509:
            return self.renew(fields)
        if command==0xa50b:
            result=self.commit('inventory.repair',gids=fields['PropRenewLst'])
            updates=self.updates(result,CONSTANTS['EUpdatePropertyType_RENEW'])
            return [packet(0xa50c,dict(Result=CONSTANTS['SHOP_ERR_SUCC'],
                PropGidLst=[row['gid'] for row in result['items']],ConsumeGP=result['totals']['cn2020:GP'],
                TPBalance=self.inventory.balances['cn2020:TP'])),*updates]
        if command==0xa363:
            # 主体上次已出售也必须先恢复原幂等回执，不能在缓存里查不到就提前拒绝。
            result=self.commit('inventory.sell',gid=fields['PropGid'])
            updates=self.updates(result,CONSTANTS['EUpdatePropertyType_DROPPROP'])
            return [packet(0xa364,dict(Result=CONSTANTS['ZONE_ERR_SUCC'],PropGid=fields['PropGid'],
                ItemId=result['items'][0]['item_id'],AddGP=result['totals']['cn2020:GP'])),*updates]
        if command==0xa375:
            # 主体已在上次成功事务中消耗时仍需进入幂等回执恢复；归属和配方主体由服务端锁内检查。
            from revival.native_containers import catalog, is_container
            if is_container(fields['FortuneKeyType']):
                # 当前开箱请求没有主体GID，服务端按协议映射从本人有效库存选箱。
                if fields['ItemId'] or fields['ItemGid']:
                    raise LoginError('INVALID')
                result=self.commit('inventory.open',container_protocol=fields['FortuneKeyType'],
                    count=fields['FortuneKeyCount'],rule_version=catalog()['version'])
                updates=self.updates(result,43)
                return [*updates,packet(0xa376,dict(Result=CONSTANTS['EXCHANGE_ERR_SUCC'],
                    FortuneKeyType=result['container_protocol'],FortuneKeyCount=result['count'],
                    Props=[self.prop(row) for row in result['grants']]))]
            if fields['FortuneKeyCount']!=1:
                raise LoginError('INVALID')
            rules=self.view('inventory.recipes',item_id=fields['ItemId'])
            recipe=next((r for r in rules['recipes'] if r['protocol_value']==fields['FortuneKeyType']),None)
            if recipe is None:
                raise LoginError('ITEM_ACTION')
            # PredictProp是客户端预测值，绝不能用来决定奖励ID、数量或期限。
            result=self.commit('inventory.craft',gid=fields['ItemGid'],recipe_id=recipe['recipe_id'],rule_version=rules['rule_version'])
            updates=self.updates(result,43)
            props=[self.prop(self.item(row['gid'])) for row in result['delta']['items']
                   if row['item_id']==recipe['AchieveItemId']]
            return [packet(0xa376,dict(Result=CONSTANTS['EXCHANGE_ERR_SUCC'],FortuneKeyType=fields['FortuneKeyType'],
                       FortuneKeyCount=1,Props=props)),*updates]
        if command in (0x1112,0x1114):
            # 当前元数据将这两个请求放在RankDaily族，不能把排行领奖误接为签到。
            raise LoginError('ITEM_ACTION')
        if command in (0xc106,0xc107,0xc108,0xc110,0xc117,0xc119):
            return self.checkin(command,fields)
        if command in (0xfa0a,0xfa0c,0xfa0e,0xfa16):
            return self.lottery(command,fields)
        if command in (0xfa18,0xfa1a,0xfa1c,0xfa1e):
            return self.custom_pool(command,fields)
        if command in (0xfa20,0xfa23):
            return self.workshop(command,fields)
        if command==0xfa05:
            rule=self.view('draw',pool='workshop')
            recipe=next((r for r in rule['definition'].get('recipes',[]) if r['index']==fields['Index']),None)
            if recipe is None:
                raise LoginError('INVALID')
            self.native_prizes(recipe['pool'])  # 在扣材料前验证全部产出可由当前消息表达。
            result=self.commit('draw.commit',pool='workshop',rule_id=rule['id'],recipe_index=fields['Index'])
            return [packet(0xfa06,dict(ExchangeResult=CONSTANTS['LOTTERY_ERR_SUCC'],Index=fields['Index'])),*self.updates(result,39)]
        return self.social(command,fields)

    def renew(self, fields):
        """A509期限续费沿用原实例；商品价格由服务端重算，不能变成重新购买或耐久维修。"""
        if fields['ConvertMP'] or fields['VoucherId']:
            raise LoginError('QUOTE_CHANGED')
        lines=[dict(commodity_id=fields['CommodityId'],price_index=fields['PriceIndex'])]
        quote=self.view('shop.quote',lines=lines,renew_gid=fields['PropGid'])
        line=quote['lines'][0]
        if fields['Price']!=line['price'] or fields['PayType']!={'cn2020:GP':1,'cn2020:TP':2,'cn2020:MP':3}.get(line['currency']):
            raise LoginError('QUOTE_CHANGED')
        result=self.commit('shop.renew',gid=fields['PropGid'],lines=lines,quote=quote)
        updates=self.updates(result,CONSTANTS['EUpdatePropertyType_ADDAVAILD'])
        row=self.item(fields['PropGid'])
        # 资产增量会重绘仓库并关闭现有弹层；先更新实际期限/余额，再发送成功回调，
        # 避免OnRepairTimeComplete刚创建的“增加3天”提示立即被A00A销毁。
        return [*updates,packet(0xa50a,dict(Result=CONSTANTS['SHOP_ERR_SUCC'],PropGid=fields['PropGid'],
            CommodityId=fields['CommodityId'],PayType=fields['PayType'],
            # 当前1530F20消费者把正文+0xA直接写入RenewalPropInfo.AddAvailHours。
            # 此字段是本次购买时长，不能把原有剩余时间再次计入成功提示；
            # 精确到期时刻仍通过后续A00A的ObtainTime/AvailbilityHour组合同步。
            AvailableHour=line['rewards'][0]['duration_seconds']//3600,
            Durability=row.get('state',{}).get('durability',row['durability_max']),
            ConsumeGP=quote['totals'].get('cn2020:GP',0),ConsumeTP=quote['totals'].get('cn2020:TP',0),
            ConsumeMP=quote['totals'].get('cn2020:MP',0),TPBalance=self.inventory.balances['cn2020:TP']))]

    def purchase(self, fields):
        """整车核价后按接收者决定本人购买或赠送；BuyType只回显，不据此绕过价格及持有者验证。"""
        if fields['ConvertMP']:
            raise LoginError('QUOTE_CHANGED')
        recipient=fields['Consignne'] or self.inventory.game_id
        lines=[{'commodity_id':r['CommodityId'],'price_index':r['PriceIndex']} for r in fields['CommodityList']]
        quote=self.view('shop.quote',lines=lines)
        for offered,requested in zip(quote['lines'],fields['CommodityList']):
            if requested['Price']!=offered['price'] or requested['VoucherId'] or requested['Props']:
                raise LoginError('QUOTE_CHANGED')
        gift=recipient!=self.inventory.game_id
        result=self.commit('shop.gift' if gift else 'shop.purchase',lines=lines,quote=quote,
                           **({'recipient_game_id':recipient} if gift else {}))
        updates=self.updates(result,2)
        # 回执实例按行匹配，每个独立实例只能出现在一个商品结果中。
        commodities=[]
        for index,line in enumerate(quote['lines']):
            props=[]
            # 新回执逐行保存GID，同一种计数材料在两行购买可重复引用，不消耗待匹配列表。
            grants=result.get('line_grants')
            rows=grants[index] if grants is not None else [r for r in result['delta']['items'] if r['item_id'] in {x['item_id'] for x in line['rewards']}]
            for row in rows:
                durability=row['durability'] if 'durability' in row else self.item(row['gid'])['durability_max']
                props.append(dict(GID=row['gid'],ItemId=row['item_id'],AvalidHour=-1 if row['expires_at'] is None else max(0,math.ceil((row['expires_at']-time.time())/3600)),Durability=durability))
            commodities.append(dict(CommodityId=line['commodity_id'],PriceIndex=line['price_index'],PropLst=props))
        values=dict(Result=CONSTANTS['SHOP_ERR_SUCC'],BuyType=fields['BuyType'],PayType=fields['PayType'],ConsignneUin=recipient,
            ConsumeTP=quote['totals'].get('cn2020:TP',0),TPBalance=self.inventory.balances['cn2020:TP'],ConsumeGP=quote['totals'].get('cn2020:GP',0),
            ConsumeMP=quote['totals'].get('cn2020:MP',0),CommodityList=commodities)
        return [packet(0xa506,values),*updates]

    def lottery(self, command, fields):
        """抽奖先查冻结规则；待确认结果跨连接找回，确认不再扣费或随机。

        当前TGameGfxUI的查询、开奖与确认均比较0x8100；已持有返还分支比较9479。
        TDR虽保留LOTTERY_ERR_SUCC=9473，它不适用于这些实际消费分支。
        """
        pool=next((k for k,v in FRAGMENTS.items() if v==fields['FragmentId']),None)
        if fields['FragmentId']==101518:
            pool='workshop'  # 仅是工坊标签，消耗材料始终来自独立配方。
        if pool is None:
            raise LoginError('INVALID')
        snapshot=self.view('draw.state',pool=pool)
        records=snapshot['receipts']
        pending=next((r for r in records if r['kind']=='draw' and r['result']['pool']==pool and not r['confirmed']),None)
        acknowledged=False
        if command==0xfa0a and pending and self.presented_draws.get(pool)==pending['id']:
            # 当前33024/9479成功分支自动发FA0A，弹层确认按钮不再发包；FA0E专用于选择补偿。
            # 只有同连接已发过该回执，才能用刷新收悉；重连首次查询不得确认或重抽。
            self.commit('draw.confirm',receipt_id=pending['id'])
            self.presented_draws.pop(pool,None)
            self.draw_update_until=0
            acknowledged=True
            pending=None
        if command==0xfa0e:
            candidate=pending or next((r for r in records if r['kind']=='draw' and r['result']['pool']==pool),None)
            if not candidate or fields['Comfirm']!=1 or candidate['result']['award']['rewards'][0].get('item_id')!=fields['PropId']:
                raise LoginError('INVALID')
            self.commit('draw.confirm',receipt_id=candidate['id'])
            self.presented_draws.pop(pool,None)
            self.draw_update_until=0
            return [packet(0xfa0f,dict(Result=CONSTANTS['ZONE_ERR_SUCC'],FragmentId=fields['FragmentId'],PropId=fields['PropId'])),*self.synchronize(CONSTANTS['EUpdatePropertyType_LOTTERYUSECOMPONENT'])]
        rule=snapshot['rule']
        entries=rule['definition'].get('pool',[])
        if pool=='selected':
            entries=[entry for entry in entries if entry['key'] in rule.get('pool_keys',rule.get('selected',[]))]
        rewards=self.native_prizes(entries)
        if command in (0xfa16,0xfa0a):
            # 自选12格走FA1F，通用FA17只能装9格；不能因尺寸差异拒绝整个页签。
            if pool=='selected' and command==0xfa16:
                rewards=[]
            if len(rewards)>(9 if command==0xfa16 else 12):
                raise LoginError('INVALID')
            definition=rule['definition']
            cost=sum(r['quantity'] for r in definition.get('materials',[]) if r['item_id']==fields['FragmentId'])
            begin=definition.get('starts_at',rule['created_at'])
            end=definition.get('ends_at') or 2147483647
            reset=datetime.fromtimestamp(end,timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M:%S')
            if command==0xfa0a:
                # 当前客户端将列表外奖品标为已合成；此字段是本轮剩余候选，不是已抽记录。
                available=[entry['rewards'][0]['item_id'] for entry in entries if entry['key'] not in rule.get('drawn_keys',[]) or (pending and entry['key']==pending['result']['award']['key'])]
                # 当前UI回调对原因39直接返回；原因40才重读材料并刷新计数。
                # 先发送已提交数量增量及正确消费原因，再刷新剩余奖池。
                return [*(self.synchronize(CONSTANTS['EUpdatePropertyType_LOTTERYUSECOMPONENT']) if acknowledged else []),packet(0xfa0b,dict(Result=CONSTANTS['ZONE_ERR_SUCC'],FragmentId=fields['FragmentId'],
                    PropIds=available,VoucherCount=cost,ResetTime=reset))]
            props=[dict(PropId=r['item_id'],PropCount=r.get('quantity',1),PropPeriod=-1 if not r.get('duration_seconds') else r['duration_seconds']//3600) for r in rewards]
            return [packet(0xfa17,dict(Result=CONSTANTS['ZONE_ERR_SUCC'],Data=dict(FragmentId=fields['FragmentId'],
                PropInfos=props,ExchangeId=rule['revision'],BeginTime=begin,EndTime=end,ResetTime=reset)))]
        if fields['ExchangeId']!=rule['revision']:
            raise LoginError('RULE_CONFLICT')
        if not entries:
            raise LoginError('POOL_EMPTY')
        result=pending['result'] if pending else self.commit('draw.commit',pool=pool,rule_id=rule['id'])
        reward=result['award']['rewards'][0]
        if 'item_id' not in reward:
            raise ValueError('UNREPRESENTABLE_DRAW_REWARD')
        self.presented_draws[pool]=result['receipt_id']
        # 先展示回执再补资产，避免原生开奖期间资产回调重绘；异常关闭仍有到期补同步。
        # Flag=0表示本轮耗尽，客户端会提示自动重置；正常未结束的轮次必须为1。
        remaining=len(entries)-len(rule.get('drawn_keys',[]))-(0 if pending else 1)
        self.draw_update_until=time.monotonic()+60
        return [packet(0xfa0d,dict(Result=CONSTANTS['LOTTERY_ERR_RETURN_ONLY_PROP'] if result.get('refunded') else CONSTANTS['ZONE_ERR_SUCC'],FragmentId=fields['FragmentId'],PropId=reward['item_id'],Flag=int(remaining>0)))]

    def native_prizes(self, entries):
        """原生奖池消息只能表达单一道具；组合/货币配置在消费前拒绝，不能扣费后才编码失败。"""
        rewards=[]
        for entry in entries:
            values=entry.get('rewards',[])
            if len(values)!=1 or 'item_id' not in values[0] or values[0].get('type')=='currency':
                raise LoginError('INVALID')
            rewards.append(values[0])
        return rewards

    def checkin(self, command, fields):
        """当前每日签到族使用服务端北京时间；90001601为明确自定义活动，排行消息不发此奖励。

        日历采用Unix日界与零基今日索引的兼容合同，等待原生界面复核。正式停用时返回锁定资源。
        C117不接受任意补签日期，只允许领取今天；同日多入口仍受数据库唯一约束。
        """
        status=self.view('checkin');activity=90001601
        day=datetime.fromisoformat(status['today']).replace(tzinfo=timezone(timedelta(hours=8)))
        days=calendar.monthrange(day.year,day.month)[1]
        if command==0xc119:
            stages=[]
            if status['enabled']:
                stages=[dict(ID=activity,Stage=1,ActType=0,ButtonType=6,StartManual=1,
                    ActName='每日签到',ActContent='按北京时间每日领取一次，奖励由管理员配置',ActFlow='签到',
                    ActStgAward='道具和货币直接发入个人仓库与钱包',StartDate=int(day.replace(day=1).timestamp()),
                    StopDate=int((day.replace(day=days)+timedelta(days=1)).timestamp()))]
            return [packet(0xc11a,dict(Stages=stages,IsLock=int(not status['enabled']),AccumuMPAward=[0]*31))]
        if command==0xc106:
            rows=[dict(Date=int(day.replace(day=i).timestamp()),ActvInfo=[dict(ID=activity,Stage=[1])]
                       if status['enabled'] else []) for i in range(1,days+1)]
            return [packet(0xc101,dict(DayOfMonthBegin=int(day.replace(day=1).timestamp()),TodayIndex=day.day-1,Calendar=rows))]
        if command==0xc110:
            stages=[dict(Data=0,State=0,FinishTime=0,ClearDate=0) for _ in range(10)]
            stages[0].update(Data=int(status.get('claimed_today',False)),State=int(status.get('claimed_today',False)))
            return [packet(0xc104,dict(ActvList=[dict(Id=activity,StageInfo=stages)] if status['enabled'] else []))]
        if fields.get('ID',activity)!=activity or fields.get('Stage',1)!=1 or (command==0xc107 and fields['Type']!=6):
            raise LoginError('INVALID')
        if not status['enabled']:
            raise LoginError('ACTIVITY_DISABLED')
        if command==0xc108:
            return [packet(0xc109,dict(Result=35329,ID=activity,Stage=1,DeltaValue=int(status.get('claimed_today',False))))]
        result=self.commit('checkin.claim',campaign='default',rule_id=status['rule_id'])
        return [packet(0xc109,dict(Result=35329,ID=activity,Stage=1,DeltaValue=1)),*self.updates(result,32),
                *self.checkin(0xc110,{})]

    def custom_pool(self, command, fields):
        """自选候选/提交/查询/重置使用真实持久选择；兼容版本免费重置且不丢弃待确认结果。"""
        rule=self.view('draw',pool='selected')
        entries=rule['definition']['pool']
        rewards=self.native_prizes(entries)
        if len(rewards)>256 or len({r['item_id'] for r in rewards})!=len(rewards):
            raise LoginError('INVALID')
        selected=rule.get('selected',[])
        pool_keys=rule.get('pool_keys',selected)
        if command==0xfa1a:
            ids=fields['SelectItem']
            mapping={entry['rewards'][0]['item_id']:entry['key'] for entry in entries}
            if any(i not in mapping for i in ids):
                raise LoginError('INVALID')
            result=self.commit('draw.select',rule_id=rule['id'],selected=[mapping[i] for i in ids])
            selected=result['selected']
            pool_keys=result.get('pool_keys',selected)
        elif command==0xfa1c:
            if fields['NZPoint'] or fields['PropId']:
                raise LoginError('QUOTE_CHANGED')
            self.commit('draw.reset',rule_id=rule['id'])
            return [packet(0xfa1d,dict(Result=CONSTANTS['ZONE_ERR_SUCC']))]
        chosen=entries if command==0xfa18 else [e for e in entries if e['key'] in pool_keys]
        # 当前TDR ECustomExgType_UserDefined=1、Rand_Mid=3，0不是有效奖项类型。
        items=[dict(Type=1 if command==0xfa18 or e['key'] in selected else 3,PropId=e['rewards'][0]['item_id'],PropCount=e['rewards'][0].get('quantity',1),
            PropPeriod=-1 if not e['rewards'][0].get('duration_seconds') else e['rewards'][0]['duration_seconds']//3600) for e in chosen]
        values=dict(Result=CONSTANTS['LOTTERY_ERR_CUSTOMEXCHANGE_NOT_READY'] if command==0xfa1e and not items else CONSTANTS['ZONE_ERR_SUCC'],Items=items)
        if command!=0xfa18:
            values['ResetCost']=0
        return [packet(command+1,values)]

    def social(self, command, fields):
        """好友请求和消息进入接收者事件箱；这里的应答不冒充对端已经阅读。"""
        success=CONSTANTS['SNS_ERR_SUCC']
        if command in (0xa31f,0xa321,0xa322):
            if command==0xa31f:
                self.commit('friend.group_create',name=fields['GroupName'])
            elif command==0xa321:
                self.commit('friend.group_delete',group_id=fields['GroupId'])
            else:
                group=fields['SnsGroupInfo']
                self.commit('friend.group_rename',group_id=group['GroupId'],name=group['GroupName'])
            view=self.view('friends')
            return [packet(0xa320,dict(Result=success,SnsGroupInfo=[dict(GroupId=g['group_id'],GroupName=g['name']) for g in view['groups']])),*self.friend_snapshot(view)]
        if command in (0xa30b,0xa30d):
            target=self.view('player',game_id=fields['EnemyUin'],nickname=fields.get('EnemyNickName',''))
            self.commit('friend.block' if command==0xa30b else 'friend.unblock',peer_game_id=target['game_id'])
            return [packet(command+1,dict(Result=success,**{('AddEnemyUin' if command==0xa30b else 'DelEnemyUin'):target['game_id']})),*self.friend_snapshot()]
        if command==0xa303:
            friends=self.view('friends')['friends']
            wanted=set(fields['FriendUinLst'])
            rows=[dict(FriendUin=r['game_id'],Status=int(r['online'])) for r in friends if r['status']=='ACCEPTED' and (not wanted or r['game_id'] in wanted)]
            return [packet(0xa304,dict(Type=fields['Type'],FriendStatusLst=rows[i:i+200])) for i in range(0,max(1,len(rows)),200)]
        if command==0xa30f:
            found=self.view('player',game_id=fields['FriendUin'],nickname=fields['FriendNickName'])
            return [packet(0xa310,dict(Result=success,FriendUin=found['game_id'],FriendNickName=found['nickname']))]
        if command==0xa305:
            target=self.view('player',game_id=fields['RespondentUin'],nickname=fields['RespondentNickname'])
            self.commit('friend.request',peer_game_id=target['game_id'])
            return []  # 对方收到A306，接受或拒绝才生成A308；不能提前伪造接受。
        if command==0xa307:
            # A307的Result是玩家决定，不是SNS_ERR_SUCC：本构建实收同意为33537，拒绝为769。
            # 未知值不能落到拒绝分支，否则损坏/旧客户端请求会无声删除仍有效的申请。
            action={CONSTANTS['SNS_ADDFRID_AGREE']:'friend.accept',
                    CONSTANTS['SNS_ADDFRID_REFUSE']:'friend.reject'}.get(fields['Result'])
            if action is None:
                raise LoginError('INVALID')
            self.commit(action,peer_game_id=fields['ProposerUin'])
            return []
        if command==0xa309:
            self.commit('friend.delete',peer_game_id=fields['FriendUin'])
            return [packet(0xa30a,dict(Result=success,DelFriendUin=fields['FriendUin'])),*self.friend_snapshot()]
        if command==0xa311:
            self.commit('friend.edit',peer_game_id=fields['FriendUin'],group=str(fields['NewGroupId']))
            return [packet(0xa312,dict(Result=success,FriendUin=fields['FriendUin'])),*self.friend_snapshot()]
        if command in (0xa329,0xa405):
            target=fields['FriendUin'] if command==0xa329 else fields['RecipientUin']
            message=fields['Msg'] if command==0xa329 else fields['ChatMsgBuf']
            if command==0xa405 and (fields['C2ZNP2PType']!=CONSTANTS['ECHATTYPE_P2PFriend'] or fields['ChatPkg']):
                raise LoginError('INVALID')
            self.commit('friend.message',peer_game_id=target,message=message)
            return [packet(0xa32a,dict(Result=success,FriendUin=target))] if command==0xa329 else []
        raise ValueError('UNBOUND_SOCIAL_COMMAND')

    def friend_snapshot(self, view=None, *, changed_only=False):
        """登录和关系改变后发送本人完整好友/黑名单/分组；在线变化单独来自有效游戏心跳。"""
        view=view or self.view('friends')
        fingerprint=json.dumps(view,sort_keys=True)
        if changed_only and fingerprint==self.last_friend_state:
            return []
        friends=[];enemies=[];statuses=[]
        for row in view['friends']:
            if row['status'] not in ('ACCEPTED','BLOCKED'):
                continue
            item=dict(Uin=row['game_id'],GroupId=int(row['group'] or 0),Remark=row['remark'])
            if row['status']=='ACCEPTED':
                friends.append(item);statuses.append(dict(FriendUin=row['game_id'],Status=int(row['online'])))
            else:
                enemies.append(item)
        groups=[dict(GroupId=g['group_id'],GroupName=g['name']) for g in view.get('groups',[])]
        packets=[packet(0xa323,dict(FriendList=friends,EnemyList=enemies,GroupList=groups)),
                 packet(0xa304,dict(Type=0,FriendStatusLst=statuses))]
        self.last_friend_state=fingerprint
        return packets

    def poll(self):
        """轮询未送达社交事件；仅已编码并成功sendall的事件才由调用方确认，未知类型保留。"""
        if time.monotonic()<self.next_poll:
            return []
        snapshot=self.view('session.sync',after_revision=self.inventory.revision,
            include_inventory=time.monotonic()>=self.draw_update_until,
            include_room=True,room_rule_version=ROOM_VERSION,
            kinds=['friend.requested','friend.accepted','friend.rejected','friend.deleted','friend.message','clan.invited','clan.applied','clan.application.result','room.changed','room.left','room.invited'])
        packets=[];self.event_ids=[]
        change=snapshot['inventory']
        if change:
            if change.get('reload'):
                packets.extend(self.synchronize())
            else:
                if change.get('delta'):
                    packets.extend(self.apply_updates(change['delta'],2))
                if change.get('more'):
                    packets.extend(self.synchronize())
        packets.extend(self.friend_snapshot(snapshot['friends'],changed_only=True))
        packets.extend(self.clan_snapshot(snapshot['clan'],changed_only=True))
        packets.extend(self.room_snapshot(snapshot.get('room')))
        for row in snapshot['events']:
            payload=row['payload'];kind=row['kind'];wire=None
            if kind in ('friend.requested','friend.accepted','friend.rejected','friend.deleted','friend.message'):
                player=row.get('sender') or self.view('player',game_id=payload.get('game_id',payload.get('from_game_id')))
                if kind=='friend.requested':
                    wire=packet(0xa306,dict(RequestId=row['id'],ProposerUin=player['game_id'],ProposerNickname=player['nickname'],Time=row['created_at'],MsgId=row['id']))
                elif kind in ('friend.accepted','friend.rejected'):
                    # 通知同样保留专用同意/拒绝语义；通用失败仅供业务校验未通过的应答。
                    wire=packet(0xa308,dict(Result=CONSTANTS['SNS_ADDFRID_AGREE'] if kind=='friend.accepted' else CONSTANTS['SNS_ADDFRID_REFUSE'],RespondentUin=player['game_id'],RespondentNickName=player['nickname'],Time=row['created_at'],MsgId=row['id']))
                elif kind=='friend.deleted':
                    wire=packet(0xa324,dict(FriendUin=player['game_id'],FriendNickname=player['nickname'],Time=row['created_at'],MsgId=row['id']))
                else:
                    wire=packet(0xa406,dict(ZN2CP2PType=CONSTANTS['ECHATTYPE_P2PFriend'],RequisitionerUin=player['game_id'],RequisitionerNickname=player['nickname'],ChatMsgBuf=payload['message'],RecipientUin=self.inventory.game_id))
            elif kind=='clan.invited':
                wire=packet(0xb01c,dict(ClanID=payload['native_clan_id'],ClanName=payload['clan_name'],Invite_id=payload['native_application_id']))
            elif kind=='clan.application.result':
                wire=packet(0xb01b,dict(ClanID=payload['native_clan_id'],ClanName=payload['clan_name'],OffMsgID=row['id'],IsAgree=int(payload['status']=='ACCEPTED')))
            elif kind=='clan.applied':
                # 审核列表是真实权限视图；本人已撤职时保留事件，由后台后续清理而非伪造可审核。
                current=snapshot['clan']
                if current.get('role') in ('owner','officer'):
                    packets.extend(self.clan_handle(0xb015,dict(Uin=self.inventory.game_id,NickName='')))
                self.event_ids.append(row['id'])
            elif kind in ('room.changed','room.left'):
                # 房间公开状态已在同一事务快照同步；成功发送本批差异后才确认接收者事件。
                self.event_ids.append(row['id'])
            elif kind=='room.invited':
                # 房间已关闭或邀请已用/过期不再弹旧邀请；有效接收者实时视图仍不含房主口令。
                invited=self.view('room.invitation',room_rule_version=ROOM_VERSION,native_room_id=payload['native_room_id'])
                if invited:
                    wire=packet(0xa14c,dict(InviteeUin=payload['inviter_game_id'],InviteeNickname=payload['inviter_nickname'],
                        SubChannelId=invited['sub_channel'],RoomInfo=self.room_wire(invited,basic=True)))
                else:self.event_ids.append(row['id'])
            if wire:
                packets.append(wire);self.event_ids.append(row['id'])
        self.next_poll=time.monotonic()+2  # 从完成时间计间隔，避免慢网络下连续堆积轮询。
        return packets

    def delivered(self):
        """只能在本批全部帧写入连接后调用；该记录表示传输送达，不等于真人已读。"""
        if self.workshop_receipt:
            self.commit('workshop.confirm',receipt_id=self.workshop_receipt)
            self.workshop_receipt=None
        if self.event_ids:
            self.commit('events.ack',event_ids=self.event_ids)
            self.event_ids=[]
