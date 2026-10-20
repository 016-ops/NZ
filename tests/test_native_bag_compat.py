"""独立验证单机换包跳板、资格保留、在线隔离及中断恢复，不把VM测试当原生实机。"""
import hashlib
from pathlib import Path
import struct
import tempfile
import unittest
from tools import native_bag_compat as bag
from tools import native_owned_loadout_compat as owned
from tools.native_config_guard import NativeConfigGuard
from .test_native_owned_loadout_compat import Reader


class NativeBagTests(unittest.TestCase):
    """固定原包只读夹具，每个生成结果均独立解码和核对原区。"""
    @classmethod
    def setUpClass(cls):
        """只允许恢复后的原UTGame；缺私有原件明确未验证，现存错误原件仍失败。"""
        root=Path(__file__).resolve().parents[1]
        path=root/'runtime/native-recovery/coherent-2020/nz'/bag.RELATIVE
        # 公共 CI 不携带游戏原件，与相邻兼容套件一致记录显式 Skip；
        # 有原件时继续经过生成器的固定摘要门禁，损坏或临时包不能被跳过。
        if not path.is_file():raise unittest.SkipTest('固定本地换包夹具缺失，未验证真实原包')
        cls.original=path.read_bytes()
        cls.baseline=owned.fix_standalone_owned_loadout(cls.original)
        cls.candidate=bag.fix_standalone_bag(cls.original)
        cls.before=owned._Package(cls.baseline);cls.after=owned._Package(cls.candidate)
        _,old=cls.before.export(bag.FUNCTION);_,new=cls.after.export(bag.FUNCTION)
        ov,os=struct.unpack_from('<II',old,40);nv,ns=struct.unpack_from('<II',new,40)
        a,b=old[48:48+os],new[48:48+ns]
        assert b[:3]==b'\x06'+struct.pack('<H',ov-1) and b[3:28]==b'\x0b'*25
        assert b[28:28+len(a[16:-1])]==a[16:-1]
        prefix=Reader(cls.after,a,0,0)
        assert prefix.take(1)==b'\x0f'
        assert prefix.expr()[0]=='ref' and prefix.expr()[0]=='cast'
        assert (prefix.pos,prefix.vm)==(16,28)
        r=Reader(cls.after,b,28+len(a[16:-1]),ov-1);nodes={}
        while r.pos<len(b):
            pc=r.vm;op=r.take(1)[0]
            if op==7:node=('if_not',r.word(),r.expr())
            elif op==6:node=('jump',r.word())
            elif op==4:node=('return',r.expr())
            elif op==0x0f:node=('let',r.expr(),r.expr())
            elif op==0x53:node=('end',)
            else:r.pos-=1;r.vm-=1;node=('eval',r.expr())
            nodes[pc]=(node,r.vm)
        assert r.vm==nv
        assert all(n[1] in nodes or n[1]==28 for n,_ in nodes.values() if n[0] in ('if_not','jump'))
        cls.nodes=nodes;cls.start=ov-1

    def simulate(self,mode,local=True,pawn=True,eligible=True):
        """执行独立语法树，只记录原入口或ShowChangeBag，不执行真实游戏函数。"""
        refs={v:k for k,v in self.after.refs.items()};events=[]
        scope={'WorldInfo':{'NetMode':mode},'Pawn':{'eligible':eligible} if pawn else None}
        def evaluate(e,obj=scope):
            """只接受本跳板实际使用的属性、方法和比较指令。"""
            kind=e[0]
            if kind=='const':return e[1]
            if kind=='ref':return obj[refs[e[2]].rsplit('.',1)[-1]]
            if kind=='cast':return evaluate(e[2],obj)
            if kind=='context':
                value=evaluate(e[1],obj)
                return evaluate(e[2],value) if value is not None else None
            if kind=='byte_to_int':return int(evaluate(e[1],obj))
            if kind=='native':
                a,b=[evaluate(x,obj) for x in e[2]]
                if e[1]==154:return a==b
                assert e[1]==119
                return a!=b
            if kind=='call':
                assert not e[2]
                if e[1]=='IsLocalPlayerController':return local
                if e[1]=='CanChangeBag':events.append('CanChangeBag');return obj['eligible']
                assert e[1]=='ShowChangeBag'
                events.append('ShowChangeBag');return None
            raise AssertionError(e)
        pc=self.start
        for _ in range(32):
            node,nxt=self.nodes[pc]
            if node[0]=='if_not':pc=nxt if evaluate(node[2]) else node[1]
            elif node[0]=='eval':evaluate(node[1]);pc=nxt
            elif node[0]=='return':return events
            elif node[0]=='let':events.append('ORIGINAL');return events
            else:raise AssertionError(node)
        raise AssertionError('VM_LOOP')

    def test_local_eligible_calls_original_permission_and_opens_once(self):
        """合规单机玩家必须先通过Pawn原资格检查，才显示一次换包界面。"""
        self.assertEqual(self.simulate(0),['CanChangeBag','ShowChangeBag'])

    def test_online_ai_missing_pawn_and_ineligible_keep_original(self):
        """在线、非本地和空Pawn直接原路；不合资格的单机玩家也不能绕过原检查。"""
        self.assertEqual(self.simulate(1),['ORIGINAL'])
        self.assertEqual(self.simulate(0,local=False),['ORIGINAL'])
        self.assertEqual(self.simulate(0,pawn=False),['ORIGINAL'])
        self.assertEqual(self.simulate(0,eligible=False),['CanChangeBag','ORIGINAL'])

    def test_original_region_and_all_other_exports_preserved(self):
        """相对已验证配装v3仅新增一函数及其导出指针；全部其他原区逐块保持。"""
        old,_=self.before.export(bag.FUNCTION);new,_=self.after.export(bag.FUNCTION)
        self.assertEqual(self.before.refs,self.after.refs)
        patch=old['record']+32
        for index,(_,logical,size,_,_,_) in enumerate(self.before.chunks):
            expected=bytearray(self.before.clear(index))
            if logical<=patch and patch+8<=logical+size:
                expected[patch-logical:patch-logical+8]=struct.pack('<II',new['size'],new['offset'])
            self.assertEqual(self.after.clear(index)[:size],expected)

    def test_fingerprint_reapplication_and_interruption_restore(self):
        """错误原件和二次套用拒绝；异常中断恢复原UTGame及固定摘要。"""
        for raw in (self.candidate,self.original[:-1]+b'\0'):
            with self.assertRaises(ValueError):bag.fix_standalone_bag(raw)
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);client=root/'client';target=client/bag.RELATIVE
            target.parent.mkdir(parents=True);target.write_bytes(self.original)
            output=root/'evidence';output.mkdir()
            guard=NativeConfigGuard(client,output)
            with self.assertRaises(RuntimeError):
                bag.install_candidate(guard)
                self.assertEqual(hashlib.sha256(target.read_bytes()).hexdigest(),bag.CANDIDATE_SHA256)
                raise RuntimeError('simulated launcher interruption')
            NativeConfigGuard.from_plan(client,output).restore()
            self.assertEqual(target.read_bytes(),self.original)
