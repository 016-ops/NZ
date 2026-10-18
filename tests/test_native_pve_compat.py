"""单机返厅兼容包的版本拒绝、导出保持与中断恢复；不以结构验证代替原生实机验收。"""
from pathlib import Path
import hashlib
import struct
import tempfile
import unittest

from tools import native_pve_compat as pve
from tools.native_config_guard import NativeConfigGuard, content_limit


class PveCompatTests(unittest.TestCase):
    """只在临时副本安装真实固定候选包，不启动客户端、不写数据库。"""

    def test_unknown_source_rejected_before_parsing(self):
        """错误文件不进入解压或安装流程，不能把任意包当成当前构建。"""
        for blob in (b'',b'unknown',b'\0'*4096):
            with self.subTest(size=len(blob)),self.assertRaisesRegex(ValueError,'FINGERPRINT'):
                pve.fix_standalone_return(blob)

    def test_only_fixed_pve_path_has_larger_limit(self):
        """为16MiB原PVE包提供有界恢复；其他脚本和相似路径不获得该权限。"""
        self.assertEqual(content_limit(pve.RELATIVE),20*1024*1024)
        # 猎场出生时序候选为固定5.6MiB包；只开放这一精确路径，恢复沿用同一有界限制。
        self.assertEqual(content_limit('TGame/CookedPC/TGADBase.u'),8*1024*1024)
        for path in ('TGame/CookedPC/Other.u','TGame/CookedPC/pvegame.u','DefaultGame.ini'):
            self.assertEqual(content_limit(path),4*1024*1024)
        self.assertEqual(content_limit('TGame/CookedPC/tgadbase.u'),4*1024*1024)

    def test_real_candidate_preserves_original_function_and_recovers_after_restart(self):
        """重启守卫必须恢复完整原包；候选只新增Standalone分支，原报告/胜负代码逐字节保留。"""
        fixture=Path(__file__).resolve().parents[1]/'runtime/native-recovery/coherent-2020/nz'/pve.RELATIVE
        if not fixture.is_file():
            self.skipTest('固定本地客户端夹具缺失，不能报告真实包验证通过')
        original=fixture.read_bytes()
        self.assertEqual(hashlib.sha256(original).hexdigest(),pve.SOURCE_SHA256)
        before=pve._Package(original)
        variant=pve.fix_standalone_return(original)
        after=pve._Package(variant)
        _,parent,size,offset=before.end_game_export()
        _,new_parent,new_size,new_offset=after.end_game_export()
        self.assertEqual(parent,new_parent)
        self.assertEqual(after.read(offset,size),before.read(offset,size))
        old_function=before.read(offset,size)
        new_function=after.read(new_offset,new_size)
        self.assertEqual(new_function[48:48+313],old_function[48:48+313])
        self.assertEqual(new_function[-18:],old_function[-18:])
        # 条件为原生Standalone判断，false分支直接到正常Return，online仍保持原处理。
        inserted=new_function[48+313:48+313+39]
        self.assertEqual(inserted[3:],pve.STANDALONE_CONDITION+pve.RETURN_CALL)
        self.assertEqual(struct.unpack_from('<H',inserted,1)[0],516)
        self.assertEqual(struct.unpack_from('<II',new_function,40),(519,355))
        self.assertEqual(after.header[785:],before.header[785:])
        self.assertEqual(variant[8:797],variant[-800:][:789])
        with tempfile.TemporaryDirectory() as directory:
            base=Path(directory);root=base/'client';out=base/'evidence'
            path=root/pve.RELATIVE;path.parent.mkdir(parents=True);out.mkdir()
            path.write_bytes(original)
            guard=NativeConfigGuard(root,out)
            receipt=pve.install(guard)
            self.assertFalse(receipt['native_live_verified'])
            self.assertEqual(path.read_bytes(),variant)
            restored=NativeConfigGuard.from_plan(root,out).restore()
            self.assertTrue(restored['configuration_restored'])
            self.assertEqual(path.read_bytes(),original)
