"""当前配置派生的五类初始规则；缺失部分明确采用版本化自定义兼容值，不伪称官方规则。"""
from .native_features_catalog import feature_catalog, record_fields, FRAGMENTS

DEFAULT_VERSION = 'cn2020-draw-compat-v3'


def reward(item_id, quantity, hours):
    """配置中的永久-1与小时期限转换为统一资产奖励；库存保留精确过期秒数。"""
    return {'item_id':item_id,'quantity':quantity,
            'duration_seconds':None if hours==-1 else hours*3600}


def legacy_definitions():
    """保留24项原池及155条工坊配方；自选候选取原三池并集，选12项，每次消耗1碎片。

    三池与自选缺少当前服务端概率和成本，兼容初始值使用等权重、1碎片，重复奖励允许。
    这些值写入可调整的规则快照；初始化命令绝不覆盖管理员已有配置，也不启用签到。
    """
    catalog=feature_catalog()
    result={}
    candidates=[]
    for name,fragment in FRAGMENTS.items():
        if name=='selected':
            continue
        entries=[]
        for raw in catalog['lottery']['ExchangeToolsArray']:
            row=record_fields(raw)
            if row['FragmentItemId']==fragment:
                entry={'key':str(row['PropId']),'weight':1,
                       'rewards':[reward(row['PropId'],row['PropCount'],row['PropPeriod'])]}
                entries.append(entry)
                candidates.append(entry)
        result['draw:'+name]={'compatibility_version':'cn2020-draw-compat-v1',
            'basis':'CURRENT_PRIZES_CUSTOM_EQUAL_WEIGHT_AND_ONE_FRAGMENT',
            'materials':[{'item_id':fragment,'quantity':1}], 'pool':entries,'duplicate_policy':'allow'}
    unique={entry['key']:entry for entry in candidates}
    result['draw:selected']={'compatibility_version':'cn2020-draw-compat-v1',
        'basis':'CUSTOM_UNION_OF_CURRENT_POOLS_SELECT_12_EQUAL_WEIGHT_ONE_FRAGMENT_FREE_RESET',
        'materials':[{'item_id':FRAGMENTS['selected'],'quantity':1}], 'pool':list(unique.values()),
        'selection_count':12,'duplicate_policy':'allow','reset_cost':0}
    recipes=[]
    for index,raw in enumerate(catalog['lottery']['BestFragmentDatas']):
        row=record_fields(raw)
        # 当前155条均明确为1；若后续来源出现其他概率，不能当作确定成功悄悄发奖。
        if row['FragmentExchangeChance']!=1:
            raise ValueError('WORKSHOP_PROBABILITY_REQUIRES_NEW_VERSION')
        recipes.append({'index':index,'materials':[{'item_id':row['FragmentPropId'],'quantity':row['FragmentNeedNum']}],
            'pool':[{'key':str(row['AwardPropId']),'weight':1,
                     'rewards':[reward(row['AwardPropId'],row['AwardPropNum'],row['AwardPropDuration'])]}]})
    result['draw:workshop']={'compatibility_version':'cn2020-draw-compat-v1','basis':'CURRENT_ORDERED_RECIPES_PROBABILITY_ONE',
                             'recipes':recipes,'duplicate_policy':'allow'}
    return result


def round_definitions():
    """按当前界面修正选择2项、12格整轮及不重复机制，缺失概率仍用明确标注的等权重。

    原生仓库默认成本显示15；兼容v2固定每次15，不声称还原历史逐轮涨价。
    已永久持有的永久奖励退回本次成本，仍记录本轮已抽中，不能反复刷新轮次。
    """
    rules=legacy_definitions()
    for namespace,definition in rules.items():
        definition['compatibility_version']='cn2020-draw-compat-v2'
        if namespace=='draw:workshop':
            continue
        definition.update(without_replacement=True,refund_owned=True,
            basis='CURRENT_UI_NO_REPEAT_REFUND_OWNED_CUSTOM_FLAT_15_EQUAL_WEIGHT')
        definition['materials'][0]['quantity']=15
    rules['draw:selected'].update(selection_count=2,pool_size=12,
        basis='CURRENT_UI_SELECT_2_CUSTOM_UNIFORM_FILL_TO_12_FLAT_15_FREE_RESET_BEFORE_DRAW')
    return rules


def definitions():
    """工坊增加真实券池；保留其155条碎片配方。缺失数量/期限/权重采用公开的兼容值。

    当前配置只有物品、分类和两池标记，没有服务端概率与奖励数量期限。兼容v3采用
    每项1件永久、等权重、每日免费一次、每次1券；付费十连至少一项分类4以上。
    分类与池归属来自现有配置，保底阈值明确是自定义规则，发布后可在后台调整。
    """
    rules=round_definitions()
    pool=[]
    for index,raw in enumerate(feature_catalog()['lottery']['DailyLotteryPoolArray']):
        row=record_fields(raw)
        pool.append({'key':str(index),'weight':1,'tier':row['DailyLotteryItemType'],
            'free':bool(row['bIsFreePool']),'paid':bool(row['bIsChargePool']),
            'show_id':row['ShowType'],'pic_id':row['NewIconID'],
            'reward':reward(row['DailyLotteryItemId'],1,-1)})
    rules['draw:workshop'].update(compatibility_version=DEFAULT_VERSION,
        basis='CURRENT_RECIPES_AND_VOUCHER_POOLS_CUSTOM_ONE_PERMANENT_EQUAL_WEIGHT_TIER4_TEN',
        vouchers={'free_material':{'item_id':103745,'quantity':1},
                  'paid_material':{'item_id':103746,'quantity':1},
                  'refund_owned':True,'ten_min_tier':4,'pool':pool})
    return rules
