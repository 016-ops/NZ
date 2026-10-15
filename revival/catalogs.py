"""显式加载独立合成 PvE 配置；规则版本与普通合成目录分离，不写入当前客户端库。"""
import copy
import json
from pathlib import Path


def pve_catalog(base):
    """把可读的模板展开为冻结规则；每张测试图都有独立规则 ID 和明确测试证据。"""
    profile = json.loads((Path(__file__).resolve().parents[1] / 'config/test-pve-profile.json').read_text(encoding='utf-8'))
    if base['id'] != profile['base_profile']:
        raise ValueError('PvE test profile base version mismatch')
    catalog = copy.deepcopy(base)
    catalog['id'] = profile['id']
    catalog['maps'] = {}
    for key, definition in profile['modes'].items():
        common = copy.deepcopy(profile['common_rules'])
        rules = dict(common, **copy.deepcopy(definition), id=profile['id'] + ':' + key, evidence='TEST_PROFILE')
        for stage in rules['stages']:
            stage['waves'] = [[copy.deepcopy(common[name]) for name in wave] for wave in stage['waves']]
        catalog['maps'][key] = {'capacity': definition['capacity'], 'min_players': definition['min_players'],
                                'difficulties': ['default'], 'channels': ['local'], 'pve_rules': rules, 'evidence': 'TEST_PROFILE'}
    return catalog
