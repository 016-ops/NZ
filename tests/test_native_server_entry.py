"""原生server诊断入口的配置/资源边界测试；不启动真实游戏或依赖数据库。"""
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tools.native_email_boot import current_server_map_arguments


class NativeServerEntryTests(unittest.TestCase):
    """启动参数必须由同一固定配置唯一推导，缺失资源或输入注入均拒绝。"""

    def workspace(self, directory, extra=''):
        """创建含地图和精确SV模式前缀的临时夹具，测试函数仍真实读取并散列配置。"""
        root=Path(directory)
        config=root/'TGame/Config/DefaultGame.ini'
        config.parent.mkdir(parents=True)
        config.write_text('[Engine.GameInfo]\n'
            '+DefaultMapPrefixes=(Prefix="SV",GameType="PVEGame.TGSVGame")\n'
            '+GameMapSettings=(MapId=1,MapName="SV-First_Main")\n'+extra,encoding='utf-8')
        maps=root/'TGame/CookedPC/Maps'
        maps.mkdir(parents=True)
        (maps/'SV-First_Main.udk').write_bytes(b'fixture'*30)
        return root,hashlib.sha256(config.read_bytes()).hexdigest()

    def test_map_mode_derived_from_current_config_and_existing_resource(self):
        """模式参数精确取当前前缀映射，不能把相似前缀或外部模式静默套入。"""
        with tempfile.TemporaryDirectory() as directory:
            root,sha=self.workspace(directory)
            with patch('tools.native_ui_config.GAME_CONFIG_SHA256',sha):
                self.assertEqual(current_server_map_arguments(root,'SV-First_Main'),
                    ('server','SV-First_Main?game=PVEGame.TGSVGame','-unattended','-nosplash'))
                with self.assertRaisesRegex(ValueError,'CURRENT_SERVER_MAP_NOT_UNIQUE_IN_CONFIG'):
                    current_server_map_arguments(root,'SV3-First_Main')

    def test_changed_config_missing_resource_and_unsafe_name_rejected(self):
        """未知配置先失败；缺少实际地图或带参数注入/路径穿越的名字不能启动。"""
        with tempfile.TemporaryDirectory() as directory:
            root,sha=self.workspace(directory)
            with self.assertRaisesRegex(ValueError,'CURRENT_SERVER_MAP_CONFIG_MISMATCH'):
                current_server_map_arguments(root,'SV-First_Main')
            with patch('tools.native_ui_config.GAME_CONFIG_SHA256',sha):
                for name in ('../SV-First_Main','SV-First_Main?game=Other.Class','SV-First_Main -server'):
                    with self.subTest(name=name),self.assertRaisesRegex(ValueError,'INVALID_CURRENT_SERVER_MAP_NAME'):
                        current_server_map_arguments(root,name)
                (root/'TGame/CookedPC/Maps/SV-First_Main.udk').rename(root/'TGame/CookedPC/Maps/Other.udk')
                with self.assertRaisesRegex(ValueError,'CURRENT_SERVER_MAP_RESOURCE_MISSING_OR_AMBIGUOUS'):
                    current_server_map_arguments(root,'SV-First_Main')

    def test_duplicate_logical_map_or_conflicting_mode_rejected(self):
        """重复逻辑定义与冲突模式都不能由扫描顺序任意选择。"""
        for extra,reason in [('+GameMapSettings=(MapId=2,MapName="SV-First_Main")\n',
                              'CURRENT_SERVER_MAP_NOT_UNIQUE_IN_CONFIG'),
                             ('+DefaultMapPrefixes=(Prefix="SV",GameType="Other.Game")\n',
                              'CURRENT_SERVER_MAP_MODE_AMBIGUOUS')]:
            with tempfile.TemporaryDirectory() as directory:
                root,sha=self.workspace(directory,extra)
                with patch('tools.native_ui_config.GAME_CONFIG_SHA256',sha):
                    with self.assertRaisesRegex(ValueError,reason):
                        current_server_map_arguments(root,'SV-First_Main')
