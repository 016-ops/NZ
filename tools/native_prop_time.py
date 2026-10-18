"""原生道具日历期限投影；使用获得时刻和可用小时组合，精确保留服务器到期秒数。"""
import time


def prop_time(expires_at, now=None, *, consume_type=0):
    """限时基准与小时相加等于expires_at；日历型永久沿当前商城200年占位表达。

    当前日历型角色不检查AvailabilityHour=-1，而从DueDataTime计算剩余小时，
    且明确将至少876000小时的角色显示为永久。这里只作客户端投影，数据库仍保持NULL期限。
    """
    now=int(time.time()) if now is None else now
    if expires_at is None:
        if consume_type==1:
            return dict(ObtainTime=now,Validity=0,AvailbilityHour=1752000)
        return dict(ObtainTime=0,Validity=0,AvailbilityHour=-1)
    hours=max(0,(expires_at-now+3599)//3600)
    return dict(ObtainTime=expires_at-hours*3600,Validity=0,AvailbilityHour=hours)
