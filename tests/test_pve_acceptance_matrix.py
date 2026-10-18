"""逐图范围回归：旧塔防、缺资源及未识别模式都不得静默缩小验收分母。"""
from pathlib import Path
import tempfile
import unittest

from tools.pve_acceptance_matrix import build, classify, difficulty_catalog
from tests.test_native_map_streaming import streaming_fixture


class PveAcceptanceMatrixTests(unittest.TestCase):
    """使用独立最小配置验证分类边界，静态盘点始终保留所有实机待验收状态。"""

    def test_legacy_and_current_tower_classes_share_scope(self):
        """实际ATDGameInfo与TGATD均纳入塔防，名称相似的非玩法类不能误入。"""
        for cls in ('ATDGame.ATDGameInfo','TGATD1Game.TGATD1Game','TGATD15Game.TGATD15Game'):
            with self.subTest(cls=cls):
                modes,basis=classify('ATD-Capital_17_Main',[cls])
                self.assertEqual(modes,['tower_defense'])
                self.assertEqual(basis,'CURRENT_CONFIG_PREFIX_CLASS')
        self.assertEqual(classify('Other-Map',['ATDGame.Other','Other.ATDGameInfo'])[0],[])

    def test_old_defense_and_name_fallback_are_distinguished(self):
        """原PVE保卫类是配置证据，单纯IF图名仍明确保留候选标记。"""
        self.assertEqual(classify('IF-Old',['PVEGame.TGIFGame']),
                         (['defense'],'CURRENT_CONFIG_PREFIX_CLASS'))
        self.assertEqual(classify('IF2-New',[]),
                         (['defense'],'MAP_NAME_CANDIDATE_REQUIRES_CLASS_CONFIRMATION'))

    def test_global_difficulty_names_do_not_assert_per_map_support(self):
        """名称表保留原ID对应关系，缺失/重复值拒绝，绝不由全局名称生成已通过地图难度。"""
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'TGame/Config').mkdir(parents=True)
            path=root/'TGame/Config/DefaultEngine.ini'
            self.assertEqual(difficulty_catalog(root)['status'],'SOURCE_MISSING')
            text=('[UTGame.TGOnlineLogic]\n'
                  '+TGPVESubModeTypeMappings=(SubModeTypeNames=("简单","普通","英雄"),'
                  'SubModeTypeValues=(4101,4097,4099))\n')
            path.write_text(text,encoding='utf-8')
            result=difficulty_catalog(root)
            self.assertEqual(result['entries'],[dict(name='简单',submode_id=4101),
                             dict(name='普通',submode_id=4097),dict(name='英雄',submode_id=4099)])
            self.assertEqual(result['map_applicability'],'PENDING_PER_MAP')
            path.write_text(text.replace('4101,4097,4099','4101,4097,4097'),encoding='utf-8')
            with self.assertRaisesRegex(ValueError,'MAPPING_INVALID'):
                difficulty_catalog(root)

    def test_all_logical_ids_and_missing_maps_remain_pending(self):
        """同一物理地图的两个ID各占一行，缺资源与未知玩法保留，不输出虚假的可玩通过。"""
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            (root/'TGame/Config').mkdir(parents=True)
            (root/'TGame/CookedPC').mkdir()
            text=('[UTGame.UTGame]\n'
                  '+DefaultMapPrefixes=(Prefix="ATD",GameType="ATDGame.ATDGameInfo")\n'
                  '+GameModeSettings=(ModeName="ATDGame.ATDGameInfo",ModeId=8197)\n'
                  '+GameMapSettings=(MapId=96,MapName="ATD-Capital_17_Main",NickName="联盟大厦")\n'
                  '+GameMapSettings=(MapId=97,MapName="ATD-Capital_17_Main",NickName="同图独立ID")\n'
                  '+GameMapSettings=(MapId=999,MapName="Other-Unknown",NickName="未知保留")\n')
            (root/'TGame/Config/DefaultGame.ini').write_text(text,encoding='utf-8')
            result=build(root)
            self.assertEqual(result['all_logical_maps'],3)
            self.assertEqual(result['mode_counts'],{'tower_defense':2})
            self.assertEqual(result['unclassified_retained'],1)
            self.assertEqual(result['accepted_maps'],0)
            self.assertFalse(result['scope_confirmed_complete'])
            for row in result['maps']:
                self.assertFalse(row['playable_accepted'])
                self.assertEqual(row['difficulty_rules'],'PENDING_PER_MAP')
                self.assertTrue(all(value=='PENDING' for value in row['acceptance'].values()))
            for row in result['maps'][:2]:
                self.assertEqual(row['main_resource_status'],'MISSING_OR_UNUSABLE')
                self.assertEqual(row['configured_mode_ids'],[8197])

    def test_dynamic_levels_keep_present_missing_and_ambiguous_states(self):
        """导入表外的RoomA逐图核对存在性，缺失与重名都保留，不改变地图完整接受数。"""
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);config=root/'TGame/Config';cooked=root/'TGame/CookedPC'
            config.mkdir(parents=True);cooked.mkdir()
            (config/'DefaultGame.ini').write_text(
                '[UTGame.UTGame]\n'
                '+DefaultMapPrefixes=(Prefix="ATD",GameType="ATDGame.ATDGameInfo")\n'
                '+GameMapSettings=(MapId=1,MapName="ATD-Main",NickName="动态关卡")\n',encoding='utf-8')
            (cooked/'ATD-Main.udk').write_bytes(streaming_fixture())
            for expected in ('MISSING','PRESENT_UNVERIFIED','AMBIGUOUS'):
                # 原生流送引用按包名寻址，不能因落在upk容器而误报地图资源缺失。
                if expected=='PRESENT_UNVERIFIED':(cooked/'RoomA.upk').write_bytes(b'not-yet-verified')
                if expected=='AMBIGUOUS':(cooked/'RoomA.umap').write_bytes(b'another-resource')
                result=build(root);row=result['maps'][0]
                self.assertEqual(row['streaming_resources'][0]['streaming_references'][0]['status'],expected)
                self.assertEqual(result['accepted_maps'],0)
                self.assertEqual(row['dependency_closure'],'PENDING')


if __name__=='__main__':
    unittest.main()
