"""指定节点默认地图配置的源、范围和可恢复性边界；不启动或修改任何游戏进程。"""
import hashlib
from pathlib import Path
import tempfile
import unittest
from tools.native_room_server_config import variant,SOURCE_SHA256,SHARED
from tools.client_catalog import decode_resource,parse_ini
from tools.native_config_guard import NativeConfigGuard


class NativeRoomServerConfigTests(unittest.TestCase):
    """真实固定源仅作只读输入，所有恢复写入均在临时小目录。"""
    @classmethod
    def setUpClass(cls):
        """若缺验收资源明确跳过，不能用合成配置声称当前构建匹配。"""
        path=Path(__file__).resolve().parents[1]/'runtime/native-recovery/acceptance-83/nz/TGame/Config/DefaultEngine.ini'
        if not path.is_file():raise unittest.SkipTest('固定原始配置副本不可用')
        cls.original=path.read_bytes()
        if hashlib.sha256(cls.original).hexdigest()!=SOURCE_SHA256:raise ValueError('Original server config differs')

    def test_two_priority_maps_have_exact_variants_and_keep_logging(self):
        """两张优先图只扫描共享池，防止75个重复包再次弹窗；日志与难度语义保持原件。"""
        expected={168:('ATD-Capital_34_Main','026f0582145318499ce74c6e5e1c56d88a8f7b4a539b042ca2ae21690d9e41b6'),
                  113:('AD-Capital_20_Main','63b2de71f1406bf4dea4c6e858cfd7d20ab7d70770c2c92a0aade235050000be')}
        for map_id,(name,sha) in expected.items():
            raw=variant(self.original,map_id=map_id);self.assertEqual(hashlib.sha256(raw).hexdigest(),sha)
            text,_=decode_resource(raw)
            self.assertEqual(text.count(name+'.udk?listen'),2);self.assertIn('Port=17777',text)
            self.assertIn('\nSeekFreePCPaths='+SHARED,text)
            # 同时保留相对私有根和+共享根是已发生的原生启动阻塞；不能通过屏蔽弹窗规避。
            roots=[line for line in text.splitlines() if line.lstrip('+-!').startswith('SeekFreePCPaths=')]
            self.assertEqual(roots,['SeekFreePCPaths='+SHARED])
            self.assertNotIn('SeekFreePCPaths=..\\..\\TGame\\CookedPC',text)
            self.assertIn('-Suppress=Warning',text);self.assertIn('-Suppress=Init',text)
            self.assertNotIn('?Difficulty=',text)

    def test_bad_source_missing_map_and_out_of_range_port_are_rejected(self):
        """任何源位变、缺主图、布尔和越界端口均在安装之前拒绝。"""
        changed=bytearray(self.original);changed[-1]^=1
        with self.assertRaises(ValueError):variant(bytes(changed))
        for arguments in (dict(map_id=128),dict(map_id=99999),dict(map_id=True),dict(port=1023),dict(port=32768),dict(port=True)):
            with self.assertRaises(ValueError):variant(self.original,**arguments)

    def test_private_instance_config_restores_without_touching_shared_pool(self):
        """恢复守卫只更改独立实例配置，不创建硬链接、不修改共享资源或客户端原件。"""
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)/'instance';path=root/'TGame/Config/DefaultEngine.ini';path.parent.mkdir(parents=True)
            path.write_bytes(self.original)
            output=Path(directory)/'evidence';output.mkdir();guard=NativeConfigGuard(root,output)
            try:
                guard.apply('TGame/Config/DefaultEngine.ini',variant(self.original))
                self.assertNotEqual(path.read_bytes(),self.original)
            finally:
                self.assertTrue(guard.restore()['configuration_restored'])
            self.assertEqual(path.read_bytes(),self.original);self.assertEqual(path.stat().st_nlink,1)
