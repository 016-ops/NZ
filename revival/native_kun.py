"""从真实有效库存推导鲲套核心进化等级，不把验收满级当作所有玩家的默认值。"""
from .native_features_catalog import crafting_catalog


def snapshot(items, now):
    """沿当前DefaultUI的0/1/2/3/4种核心阈值推导1至5级；过期与重复核心不累计。

    核心为账号持有的解锁种类，94槽只选择一种当前主动技能，并非把七种核心塞进同槽。
    原生界面要求103438誓约；天鲲与魔鲲共享同一套核心种类和进化等级。
    """
    active={row['item_id'] for row in items if row.get('quantity',0)>0 and
            (row.get('expires_at') is None or row['expires_at']>now)}
    kernels=sorted(active.intersection(crafting_catalog()['kernels']))
    weapons=sorted(active.intersection((103428,103716)))
    return dict(rule_version='cn2020-kun-owned-kernels-v1',weapons=weapons,
                kernels=kernels,level=1+min(4,len(kernels)) if weapons else 0,
                suit_license=103438 in active,all_kernels_owned=len(kernels)==7)
