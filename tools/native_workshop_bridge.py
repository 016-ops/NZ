"""当前TDR FA20至FA24工坊券协议；结果回放和传输确认使用数据库回执，不在桥接生成奖励。"""
import time
from adapters.cn_current.features import packet, metadata
from tools.email_launcher import LoginError


class NativeWorkshopMessages:
    """混入游戏功能会话，复用鉴权、幂等日志及延迟资产同步。"""

    def workshop_result(self, result):
        """原生开奖只展示本次1/10项；资产通知延后，避免动画期间重绘仓库销毁弹层。"""
        self.draw_update_until=time.monotonic()+12
        self.workshop_receipt=result['receipt_id']
        items=[]
        for award in result['awards']:
            reward=award['entry']['reward'];seconds=reward.get('duration_seconds')
            items.append(dict(IsReturnBack=int(award['refunded']),PropId=reward['item_id'],
                              PropCount=reward.get('quantity',1),PropPeriod=-1 if seconds is None else seconds//3600))
        return [packet(0xfa22,dict(LastFreeLotteryTm=result['last_free_at'])),
                packet(0xfa21,dict(Result=metadata()['constants']['ZONE_ERR_SUCC'],Items=items))]

    def workshop(self, command, fields):
        """查询提供原始分类和图标；抽取只接0/1标志，免费/计费与材料数量由服务器决定。"""
        if command==0xfa20 and (fields['IsFree'] not in (0,1) or fields['Lottery10Time'] not in (0,1)):
            raise LoginError('INVALID')
        snapshot=self.view('workshop.state')
        rule=snapshot['rule']
        if command==0xfa23:
            if fields['Padding']!=0:
                raise LoginError('INVALID')
            if not rule:
                if snapshot['pending']:
                    return self.workshop_result(snapshot['pending']['result'])
                raise LoginError('ACTIVITY_DISABLED')
            items=[]
            for entry in rule['definition']['vouchers']['pool']:
                reward=entry['reward'];seconds=reward.get('duration_seconds')
                # 当前OnResDailyLotteryPoolInfo字节码将ItemType设为6-BelongPoolID；
                # 配置分类1至5递增，对应线包池号5至1，直接发送分类会把金色画成白色。
                items.append(dict(BelongPoolID=6-entry['tier'],PropId=reward['item_id'],
                    PropCount=reward.get('quantity',1),PropPeriod=-1 if seconds is None else seconds//3600,
                    ShowId=entry.get('show_id',0),PicId=entry.get('pic_id',0),InFreePool=int(entry['free']),InPayPool=int(entry['paid'])))
            packets=[packet(0xfa24,dict(Result=metadata()['constants']['ZONE_ERR_SUCC'],Items=items)),
                     packet(0xfa22,dict(LastFreeLotteryTm=snapshot['last_free_at']))]
            if snapshot['pending']:
                packets.extend(self.workshop_result(snapshot['pending']['result']))
            return packets
        result=self.commit('workshop.draw',rule_id=rule['id'] if rule else '',
                           mode='free' if fields['IsFree'] else 'paid',count=10 if fields['Lottery10Time'] else 1)
        return self.workshop_result(result)
