"""当前客户端持久货币兼容合同；使用独立币种键、统一资产事务和可重试的本地GM补满命令。"""
from django.db.models import Q

WALLET_VERSION = 'cn2020-wallet-width-compat-v1'
# 字段来自固定PlayerInfo元数据；offset是昵称后的线偏移而非宿主结构绝对偏移。
# 上限是当前协议整数宽度与MySQL有符号BIGINT交集，不冒充原版运营上限。
# 经验、等级、VIP点和战斗技能不是通用钱包，不在此批量改变。
WALLET_FIELDS = {
    'cn2020:TP': {'field': 'TGamePoint', 'label': 'NZ点', 'offset': 0, 'format': 'i', 'max': 2147483647},
    'cn2020:GP': {'field': 'GoldPoint', 'label': 'GP', 'offset': 4, 'format': 'i', 'max': 2147483647},
    'cn2020:MP': {'field': 'MonthPoint', 'label': '月券', 'offset': 8, 'format': 'i', 'max': 2147483647},
    'cn2020:HP': {'field': 'HappyPoint', 'label': '欢乐点', 'offset': 12, 'format': 'i', 'max': 2147483647},
    'cn2020:EP': {'field': 'EvolutionPoint', 'label': '进化点', 'offset': 26, 'format': 'i', 'max': 2147483647},
    'cn2020:SP': {'field': 'StarPoint', 'label': '星星', 'offset': 252, 'format': 'H', 'max': 65535},
    'cn2020:MECHA': {'field': 'MechaPoint', 'label': '机甲点', 'offset': 276, 'format': 'Q', 'max': 9223372036854775807},
}


def wallet_catalog():
    """返回独立目录定义；不会覆盖合成GP/NZ，保证测试奖励与当前真实钱包隔离。"""
    return {key: dict(min=0, max=value['max'], label=value['label'], rules_version=WALLET_VERSION,
                      native_field=value['field'], evidence='COMPATIBILITY_FIELD_WIDTH')
            for key, value in WALLET_FIELDS.items()}


def snapshot(c, account_id):
    """在调用方已有的一致事务中读取七类本人余额；缺省零、越界拒绝，不截断或饱和伪造读数。"""
    from .service import Fault
    stored = {x['currency']: x['balance'] for x in c.select('wallets', Q(account_id=account_id))}
    result = {key: stored.get(key, 0) for key in WALLET_FIELDS}
    if any(type(value) is not int or not 0 <= value <= WALLET_FIELDS[key]['max'] for key, value in result.items()):
        raise Fault('NATIVE_WALLET_RANGE', 'Stored wallet exceeds the native compatibility contract', 409)
    return {'rules_version': WALLET_VERSION, 'balances': result}


def maximize(service, account_id, *, rules_version, reason):
    """仅为已启用全目录权益的指定GM账号补满钱包；事务锁下计算差额，重复/并发无重复流水。

    入口仅供本地管理命令，不授予HTTP管理权限。余额、不可改写流水、审计和outbox一起提交；
    任一步失败全部回滚。现有其他账号、合成币种、历史审计和局内金钱都不改变。
    """
    from .service import Fault, identifier
    from .assets import mutate
    if rules_version != WALLET_VERSION or any(service.catalog['currencies'].get(k) != v for k,v in wallet_catalog().items()):
        raise Fault('NATIVE_WALLET_VERSION', 'Native wallet rules must match', 409)
    if not isinstance(reason, str) or not 1 <= len(reason) <= 500:
        raise Fault('INVALID', 'A bounded grant reason is required')
    with service.db.transaction() as c:
        service._account(c, account_id)
        if not c.select('native_super_accounts', Q(account_id=account_id, enabled=True)).fetchone():
            raise Fault('NATIVE_SUPER_ACCOUNT_REQUIRED', 'Existing enabled GM entitlement required', 403)
        before = snapshot(c, account_id)['balances']
        changes = [{'type': 'currency', 'code': key, 'delta': spec['max']-before[key]}
                   for key,spec in WALLET_FIELDS.items() if before[key] < spec['max']]
        actor = {'id': 'local-management:super-account', 'role': 'owner'}
        operation = mutate(service,c,actor,account_id,changes,reason,'NATIVE_GM_WALLET_MAX') if changes else None
        result = dict(snapshot(c,account_id), status='GRANTED' if changes else 'UNCHANGED',
                      currencies_changed=len(changes), operation_count=int(bool(changes)))
        if changes:
            request_id = identifier()
            service._audit(c,actor['id'],'native.wallet.maximize',account_id,request_id,reason,
                           dict(result, operation_id=operation['operation_id']))
            service._outbox(c,'native-wallet:'+request_id,'native.wallet.changed',account_id,result)
        return result
