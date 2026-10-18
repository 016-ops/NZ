"""隔离角色装备探针的当前TDR字节与严格范围，拒绝将临时物品操作扩展为任意资产写入。"""
import hashlib
from pathlib import Path
import struct
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from tools.native_role_resources import (ROLE_EQUIP_PROBE_PROFILE, build_role_equip_probe_response,
                                         build_role_resource_probe, build_role_info_refresh, ROLE_INFO_REFRESH_PROFILE)


class NativeRoleResourceTests(TestCase):
    """当前研究分支只有固定夹具，不授予玩家真实角色，不支持删除/消耗/跨玩家装备。"""
    def request(self, operation=0, subject=1001, target=1, location=10):
        """构造已观测A008的测试替身；只用于校验严格值绑定，不执行客户端或网络。"""
        body=struct.pack('>hQQb',operation,subject,target,location)
        return struct.pack('>HHHH',0x3243,0xA008,8,len(body))+body

    def test_exact_temporary_role_response_is_deterministic_and_has_current_layout(self):
        """重复同一装备请求字节相同；结果8100加原19B结构形成当前29B应答。"""
        request=self.request()
        result=build_role_equip_probe_response(request,profile=ROLE_EQUIP_PROBE_PROFILE)
        self.assertEqual(result,bytes.fromhex('3243a009000800158100000000000000000003e900000000000000010a'))
        self.assertEqual(result,build_role_equip_probe_response(request,profile=ROLE_EQUIP_PROBE_PROFILE))

    def test_other_item_parent_location_and_destructive_operations_are_rejected(self):
        """任何非夹具操作不能借此成功：包括脱下、丢弃、另一GID、另一父对象或位置。"""
        for change in ({'operation':1},{'operation':2},{'subject':1002},{'target':0},{'location':11}):
            with self.subTest(change=change), self.assertRaisesRegex(ValueError,'ROLE_EQUIP_REQUEST_OUTSIDE_PROBE'):
                build_role_equip_probe_response(self.request(**change),profile=ROLE_EQUIP_PROBE_PROFILE)
        with self.assertRaisesRegex(ValueError,'UNSUPPORTED_ROLE_EQUIP_PROBE_PROFILE'):
            build_role_equip_probe_response(self.request(),profile='production')

    def test_changed_current_config_stops_before_encoding_any_temporary_asset(self):
        """实际配置摘要不符时不编码道具，更不尝试写客户端或真实业务库存。"""
        with TemporaryDirectory() as temporary:
            root=Path(temporary);config=root/'TGame/Config';config.mkdir(parents=True)
            (config/'DefaultItemLibrary.ini').write_bytes(b'changed config')
            with patch('tools.native_role_resources.build_research_player_props') as encode:
                with self.assertRaisesRegex(ValueError,'CURRENT_ROLE_RESOURCE_CONFIG_MISMATCH'):
                    build_role_resource_probe(root,10001)
                encode.assert_not_called()

    def test_role_info_refresh_matches_current_native_nonzero_gid_fixture(self):
        """当前DLL独立完整host验证非零CurRoleGID；原生资料只引用先前临时GID，不改角色索引。"""
        wire=build_role_info_refresh(10001,b'NZRoleProbe',profile=ROLE_INFO_REFRESH_PROFILE)
        self.assertEqual(len(wire),1404)
        self.assertEqual(hashlib.sha256(wire).hexdigest(),'3da16a67a99493505605715c230a21221b7dce677944abc73480b56391dd9fd2')
        with self.assertRaisesRegex(ValueError,'UNSUPPORTED_ROLE_INFO_REFRESH_PROFILE'):
            build_role_info_refresh(10001,b'NZRoleProbe',profile='production')
