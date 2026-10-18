"""空间站旧塔防候选独立验证：虚拟机分支、原区保留、精确自身类别名和恢复。"""
from pathlib import Path
import hashlib,struct,tempfile,unittest
from tools import native_legacy_tower_owned_compat as legacy
from tools.native_hunt_compat import _Package
from tools.native_config_guard import NativeConfigGuard,content_limit
from .test_native_tower_owned_compat import decode_prefix,simulate
from .test_native_owned_loadout_compat import Reader


class LegacyTowerOwnedTests(unittest.TestCase):
    """只用固定原包在内存生成候选；不加载游戏、不修改持久资产。"""
    @classmethod
    def setUpClass(cls):
        """原件缺失明确跳过，不能将跳过当实机或单元测试通过。"""
        path=Path(__file__).resolve().parents[1]/'runtime/native-recovery/coherent-2020/nz'/legacy.RELATIVE
        if not path.is_file():raise unittest.SkipTest('旧塔防固定原件缺失')
        cls.raw=path.read_bytes();cls.old=_Package(cls.raw,reference_aliases=legacy.ALIASES)
        cls.variant=legacy.fix_standalone_legacy_tower_owned(cls.raw)
        cls.new=_Package(cls.variant,reference_aliases=legacy.ALIASES)
        cls.record,cls.function=cls.old.export(legacy.FUNCTION)
        cls.new_record,cls.modified=cls.new.export(legacy.FUNCTION)

    def test_original_online_ai_nil_and_actual_local_player_paths(self):
        """只给单机真人调用原百宝箱刷新，在线/AI/无PRI继续原函数，原Super仍执行一次。"""
        decoded,start=decode_prefix(self.old,self.function,self.modified,function=legacy.FUNCTION)
        def run(mode,**values):
            """以独立解码器求值旧PRI类型，避免使用生成器的字节计数作测试结论。"""
            return simulate(self.old,decoded,start,mode,pri_class='ATDGamePlayerReplicationInfo',**values)
        self.assertEqual(run(0),['refresh-owned-box','original'])
        for mode in (1,2,3):self.assertEqual(run(mode),['original'])
        for values in (dict(pri=False),dict(derived=False),dict(owner=False),dict(human=False),dict(player=False)):
            with self.subTest(values=values):self.assertEqual(run(0,**values),['original'])

    def test_all_three_original_debug_conditions_and_vm_spans(self):
        """核对三条原Standalone判断各占38VM字节；只禁调试附送，正常授权循环逐字节保留。"""
        os=struct.unpack_from('<I',self.function,44)[0];ns=struct.unpack_from('<I',self.modified,44)[0]
        old=self.function[48:48+os];new=self.modified[48:48+ns];pieces=[];cursor=0
        for index,(at,target) in enumerate(((706,1101),(832,1290),(1736,2558))):
            reader=Reader(self.old,old,at,0);expr=reader.expr()
            self.assertEqual((reader.pos-at,reader.vm,expr[:2]),(26,38,('native',154)))
            start=at+8+index*12
            self.assertEqual(new[start-3:start],b'\x07'+struct.pack('<H',target))
            self.assertEqual(new[start:start+38],b'\x28'+b'\x0b'*37)
            pieces.extend((old[cursor:at],b'\x28'+b'\x0b'*37));cursor=at+26
        pieces.append(old[cursor:]);expected=b''.join(pieces)
        self.assertEqual(new[19:19+len(expected[11:-1])],expected[11:-1])

    def test_original_region_and_all_other_exports_are_preserved(self):
        """仅单个导出指针变化和尾部新增函数；其余13966个导出、目录镜像、名称和导入保留。"""
        at=len(self.raw)-128;expected=bytearray(self.raw[:at])
        struct.pack_into('<II',expected,self.record['record']+32,len(self.modified),at)
        self.assertEqual(self.variant[:at],expected);self.assertEqual(self.variant[-128:],self.raw[-128:])
        self.assertEqual(self.variant[at:-128],self.modified)
        self.assertEqual(self.old.refs,self.new.refs);self.assertEqual(len(self.old.exports),13967)
        self.assertTrue(all(a==b for a,b in zip(self.old.exports,self.new.exports) if a['index']!=10044))
        self.assertEqual(hashlib.sha256(self.variant).hexdigest(),legacy.CANDIDATE_SHA256)

    def test_unknown_alias_changed_source_and_reapply_are_rejected(self):
        """默认仍拒绝重复；错别名、外来包和二次安装不能借兼容例外覆盖其他引用。"""
        for aliases in ((),(('ATDGamePlayer',-2337,7025),),legacy.ALIASES+(('foreign',-1,1),)):
            with self.assertRaises(ValueError):_Package(self.raw,reference_aliases=aliases)
        for content in (self.variant,b'foreign',self.raw[:-1]+b'X'):
            with self.assertRaisesRegex(ValueError,'SOURCE_FINGERPRINT'):legacy.fix_standalone_legacy_tower_owned(content)

    def test_interrupted_trial_restores_original_package(self):
        """8MiB仅开放确切旧塔防路径；从持久计划恢复必须等于原始5816009字节包。"""
        self.assertEqual(content_limit(legacy.RELATIVE),8*1024*1024)
        with tempfile.TemporaryDirectory() as td:
            work=Path(td)/'work';out=Path(td)/'receipt';target=work/legacy.RELATIVE
            target.parent.mkdir(parents=True);out.mkdir();target.write_bytes(self.raw)
            guard=NativeConfigGuard(work,out);legacy.install_candidate(guard)
            self.assertEqual(target.read_bytes(),self.variant)
            self.assertTrue(NativeConfigGuard.from_plan(work,out).restore()['configuration_restored'])
            self.assertEqual(target.read_bytes(),self.raw)
