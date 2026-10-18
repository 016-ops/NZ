"""当前工程消息与本人事务命令映射；成功码取当前TDR，不把材料不足断开为协议错误。"""
from adapters.cn_current.features import packet, metadata

ENGINEERING_COMMANDS={0xf402,0xf404,0xf406,0xf408}
CONSTANTS=metadata()['constants']


class NativeEngineeringMessages:
    """组合到游戏功能桥；服务端核验实际规则、持有者与数量，客户端预测字段不决定产出。"""

    def engineering_snapshot(self,view=None):
        """重登回放本人37项配方熟练度及冷却；当前DLL强制规范为Version3。"""
        view=view or self.view('engineering')
        saved={r['recipe_id']:r for r in view['progress']}
        rows=[]
        for recipe in view['catalog']['recipes']:
            level=saved.get(recipe['nSyntheticPropID'],{}).get('proficiency',0)
            maximum=recipe['ProficientDegree_Max']
            stage=1 if level*2<maximum else 2 if level<maximum else 3
            rows.append(dict(PropNO=recipe['nSyntheticPropID'],Level=level,Stage=stage))
        return packet(0xf401,dict(Uin=self.inventory.game_id,PlayerPropProductInfo=dict(Version=3,PropSpecialtyInfo=rows),
            PlayerPropCoolDownInfo=dict(Version=3,PropCoolDownInfo=[])))

    def engineering_failure(self,command,fields,code):
        """保持原请求的本人身份/配方/批量字段；失败不附带任何奖励更新。"""
        result=CONSTANTS['PROPFACTORY_ERR_NOENOUGHRESOURCE'] if code in ('MATERIALS','BALANCE') else CONSTANTS['PROPFACTORY_ERR_FAIL']
        values=dict(Result=result,Uin=self.inventory.game_id)
        if command==0xf404:
            values.update(PropIndex=fields['PropIndex'],ComposeCount=fields['ComposeCount'],ComposeType=fields['ComposeType'])
        return packet(command+1,values)

    def engineering_handle(self,command,fields):
        """消耗/随机一律由服务端完成，返回已持久结果和连续库存增量。"""
        rules=self.view('engineering');values={'rule_version':rules['rule_version']}
        if command==0xf402:
            action='engineering.decompose';values['gids']=fields['PropGIDList']
        elif command==0xf406:
            action='engineering.material_decompose';values['gids']=fields['MaterialGIDList']
        elif command==0xf408:
            action='engineering.material_compose';values['item_id']=fields['MaterialId']
        else:
            action='engineering.compose'
            values.update(recipe_id=fields['PropIndex'],mode=fields['ComposeType'],count=fields['ComposeCount'])
        result=self.commit(action,**values)
        response=dict(Result=CONSTANTS['PROPFACTORY_ERR_SUCC'],Uin=self.inventory.game_id)
        if command==0xf402:
            response['OutputItemID']=result['rewards'][0]['item_id'] if result['rewards'] else 0
        elif command==0xf404:
            response.update(PropIndex=result['recipe_id'],ComposeCount=result['count'],SuccCount=result['successes'],
                            SpecialtyLevel=result['proficiency'],ComposeType=result['mode'],CoolDownTime=result['cooldown_until'])
        reason=CONSTANTS['EUpdatePropertyType_DECOMPOSEMATERIAL' if command in (0xf402,0xf406) else 'EUpdatePropertyType_COMPOSEMATERIAL']
        replies=[packet(command+1,response),*self.updates(result,reason)]
        # F405自身携带本配方熟练度并触发客户端单项刷新。额外F401会在工程窗口打开时
        # 触发Flash全量数组刷新，把即时进度误画成满级；F401仅在登录初始化回放。
        return replies
