"""塔防真实权益候选的独立VM、原包保留和恢复检查；不将单元测试作为通关验收。"""
import hashlib
from pathlib import Path
import struct
import tempfile
import unittest

from tools import native_tower_owned_compat as tower
from tools.native_config_guard import NativeConfigGuard,content_limit
from .test_native_owned_loadout_compat import Reader


def decode_prefix(package,original,modified,*,function=tower.FUNCTION):
    """独立解析追加跳板及原Super调用，按VM宽度核对每个分支落点与参数身份。"""
    ov,os=struct.unpack_from('<II',original,40);nv,ns=struct.unpack_from('<II',modified,40)
    old=original[48:48+os];new=modified[48:48+ns]
    first=Reader(package,old,0,0);node=first.expr()
    assert node==('super',package.refs['PVEGame.PVEPlayerInventoryManager.AddAllFunctionalItem'],
                  [('ref',0,package.refs[function+'.PRI'])])
    assert first.pos==11 and first.vm==19
    assert new[:19]==b'\x06'+struct.pack('<H',ov-1)+b'\x0b'*16
    # 每处删除26磁盘字节/38VM字节，替换为38个单字节指令，原后续VM地址不移动。
    extra=3*(38-26);position=19+len(old[11:-1])+extra
    reader=Reader(package,new,position,ov-1);instructions={}
    while reader.pos<len(new):
        at=reader.vm;op=reader.take(1)[0]
        if op==7:node=('if_not',reader.word(),reader.expr())
        elif op==6:node=('jump',reader.word())
        elif op==0x53:node=('end',)
        else:
            reader.pos-=1;reader.vm-=1;node=('eval',reader.expr())
        instructions[at]=(node,reader.vm)
    assert reader.vm==nv and all(node[1] in instructions or node[1]==19
        for node,_ in instructions.values() if node[0] in ('if_not','jump'))
    return instructions,ov-1


def simulate(package,instructions,start,mode,*,pri=True,derived=True,owner=True,human=True,player=True,
             pri_class='TGATDBasePlayerReplicationInfo'):
    """只解释独立解码树；不调用游戏，记录本地真人刷新和回到原函数两个事件。"""
    refs={v:k for k,v in package.refs.items()};events=[]
    pc={'type':'pc' if human else 'ai','Player':{} if player else None} if owner else None
    rep={'type':'tower' if derived else 'other','Owner':pc} if pri else None
    scope={'WorldInfo':{'NetMode':mode}}

    def evaluate(expr,obj=scope):
        """仅支持候选实际用到的字段/类型/相等操作，未知指令明确失败。"""
        kind=expr[0]
        if kind=='const':return expr[1]
        if kind=='ref':return rep if expr[1]==0 else obj[refs[expr[2]].rsplit('.',1)[-1]]
        if kind=='byte_to_int':return int(evaluate(expr[1],obj))
        if kind=='context':
            target=evaluate(expr[1],obj)
            return evaluate(expr[2],target) if target is not None else None
        if kind=='cast':
            target=evaluate(expr[2],obj);label=refs[expr[1]]
            expected={pri_class:'tower','UTGame.TGPlayerController':'pc'}[label]
            return target if target is not None and target['type']==expected else None
        if kind=='native':
            args=[evaluate(arg,obj) for arg in expr[2]]
            if expr[1]==154:return args[0]==args[1]
            assert expr[1]==119;return args[0]!=args[1]
        if kind=='super':
            target=refs[expr[1]]
            if target==pri_class+'.GetPVEGateChestInfo':
                assert obj is rep;events.append('refresh-owned-box');return None
            assert target=='PVEGame.PVEPlayerInventoryManager.AddAllFunctionalItem'
            events.append('original');return None
        raise ValueError('UNEXPECTED_TOWER_OPCODE')

    cursor=start
    for _ in range(24):
        node,nxt=instructions[cursor]
        if node[0]=='if_not':cursor=node[1] if not evaluate(node[2]) else nxt
        elif node[0]=='eval':
            evaluate(node[1]);cursor=nxt
            if events[-1:] == ['original']:return events
        else:raise ValueError('UNEXPECTED_TOWER_PREFIX_END')
    raise ValueError('TOWER_PREFIX_LOOP')


class TowerOwnedTests(unittest.TestCase):
    """固定本构建原件测试；缺文件明确跳过，不将跳过当成功证据。"""
    @classmethod
    def setUpClass(cls):
        """仅在内存生成候选，不安装实际工作副本或启动任何游戏。"""
        path=Path(__file__).resolve().parents[1]/'runtime/native-recovery/coherent-2020/nz'/tower.RELATIVE
        if not path.is_file():raise unittest.SkipTest('固定塔防原包不可用')
        cls.raw=path.read_bytes();cls.old=tower._Package(cls.raw)
        cls.variant=tower.fix_standalone_tower_owned(cls.raw);cls.new=tower._Package(cls.variant)
        cls.record,cls.function=cls.old.export(tower.FUNCTION);cls.new_record,cls.modified=cls.new.export(tower.FUNCTION)

    def test_prefix_refreshes_only_owned_local_player_and_preserves_original(self):
        """本地真实PRI只刷新一次；在线、AI、空PRI和无Player控制器均直接执行原函数。"""
        instructions,start=decode_prefix(self.old,self.function,self.modified)
        self.assertEqual(simulate(self.old,instructions,start,0),['refresh-owned-box','original'])
        for mode in (1,2,3):self.assertEqual(simulate(self.old,instructions,start,mode),['original'])
        for values in (dict(pri=False),dict(derived=False),dict(owner=False),dict(human=False),dict(player=False)):
            with self.subTest(values=values):self.assertEqual(simulate(self.old,instructions,start,0,**values),['original'])

    def test_three_debug_conditions_skip_only_original_standalone_gifts(self):
        """原三段调试条件准确替换为false，父分支地址及其他原字节保持；原VM跨度均38。"""
        _,os=struct.unpack_from('<II',self.function,40);_,ns=struct.unpack_from('<II',self.modified,40)
        old=self.function[48:48+os];new=self.modified[48:48+ns]
        expected=[];position=0
        for index,(at,target) in enumerate(((655,1051),(993,1427),(2012,3041))):
            before=Reader(self.old,old,at,0);expr=before.expr()
            self.assertEqual((before.pos-at,before.vm),(26,38));self.assertEqual(expr[0:2],('native',154))
            start=at+8+index*12
            self.assertEqual(new[start-3:start],b'\x07'+struct.pack('<H',target))
            self.assertEqual(new[start:start+38],b'\x28'+b'\x0b'*37)
            expected.extend((old[position:at],b'\x28'+b'\x0b'*37));position=at+26
        expected.append(old[position:]);expected=b''.join(expected)
        self.assertEqual(new[19:19+len(expected[11:-1])],expected[11:-1])

    def test_original_package_and_other_16532_exports_unchanged(self):
        """除一个导出指针和末尾追加函数外，全部原字节、目录尾及其他导出均不变。"""
        at=len(self.raw)-128;expected=bytearray(self.raw[:at])
        struct.pack_into('<II',expected,self.record['record']+32,len(self.modified),at)
        self.assertEqual(self.variant[:at],expected);self.assertEqual(self.variant[-128:],self.raw[-128:])
        self.assertEqual(self.variant[at:-128],self.modified);self.assertEqual(self.new.refs,self.old.refs)
        self.assertEqual(hashlib.sha256(self.variant).hexdigest(),tower.CANDIDATE_SHA256)
        self.assertEqual(len(self.old.exports),16533)
        self.assertTrue(all(a==b for a,b in zip(self.old.exports,self.new.exports) if a['index']!=self.record['index']))

    def test_candidate_can_only_be_applied_once(self):
        """已经修改的包和外来文件不接受再次修补，不累积追加逻辑。"""
        for raw in (self.variant,b'foreign',self.raw[:-1]+b'X'):
            with self.assertRaisesRegex(ValueError,'SOURCE_FINGERPRINT'):tower.fix_standalone_tower_owned(raw)

    def test_guard_restores_exact_tower_package_after_interruption(self):
        """8MiB边界只对固定塔防路径放开，恢复读取持久计划并还原原件。"""
        self.assertEqual(content_limit(tower.RELATIVE),8*1024*1024)
        self.assertEqual(content_limit('TGame/CookedPC/Other.u'),4*1024*1024)
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)/'work';out=Path(td)/'report';target=root/tower.RELATIVE
            target.parent.mkdir(parents=True);out.mkdir();target.write_bytes(self.raw)
            guard=NativeConfigGuard(root,out);guard.apply(tower.RELATIVE,self.variant)
            self.assertEqual(target.read_bytes(),self.variant)
            self.assertTrue(NativeConfigGuard.from_plan(root,out).restore()['configuration_restored'])
            self.assertEqual(target.read_bytes(),self.raw)
