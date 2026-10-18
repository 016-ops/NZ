"""猎场出生候选的固定摘要、独立VM控制流和中断恢复测试；不替代原生地图验收。"""
from pathlib import Path
import hashlib
import struct
import tempfile
import unittest

from tools import native_hunt_compat as hunt
from tools.native_config_guard import NativeConfigGuard


class _Reader:
    """按UE3实际操作码独立计算VM地址；分支目标必须落在已解码的完整指令边界。"""
    def __init__(self,package,data,pos,vm):
        """保存独立读取游标及本包名称表，不调用生成器的表达式辅助函数。"""
        self.package=package;self.data=data;self.pos=pos;self.vm=vm
    def take(self,n,virtual=None):
        """有界读取序列化数据并更新VM指针长度，拒绝截断。"""
        value=self.data[self.pos:self.pos+n];assert len(value)==n
        self.pos+=n;self.vm+=n if virtual is None else virtual;return value
    def number(self):
        """读入32位引用，同时按引擎实际64位VM指针宽度计算偏移。"""
        return struct.unpack('<i',self.take(4,8))[0]
    def word(self):
        """读入原生16位跳转目标，不把文件偏移当VM偏移。"""
        return struct.unpack('<H',self.take(2))[0]
    def args(self):
        """参数终止标记必须存在，不能把后续指令误解为默认参数。"""
        args=[]
        while self.data[self.pos]!=0x16:args.append(self.expr())
        self.take(1);return args
    def expr(self):
        """只解码本次固定候选需要的表达式，未知操作码明确失败。"""
        op=self.take(1)[0]
        if op in (0,1):return ('ref',op,self.number())
        if op==0x19:
            obj=self.expr();skip=self.word();prop=self.number();zero=self.take(1);assert zero==b'\0'
            before=self.vm;inner=self.expr();assert self.vm-before==skip
            return ('context',obj,inner,prop)
        if op==0x38:
            cast=self.take(1)[0];assert cast==0x3a;return ('byte_to_int',self.expr())
        if op==0x1b:
            idx,num=struct.unpack('<II',self.take(8));assert num==0
            return ('call',self.package.names[idx],self.args())
        if op==0x1c:return ('super',self.number(),self.args())
        if op==0x21:
            idx,num=struct.unpack('<II',self.take(8));assert num==0
            return ('const',self.package.names[idx])
        if op==0x23:return ('const',list(struct.unpack('<fff',self.take(12))))
        if op==0x1e:return ('const',struct.unpack('<f',self.take(4))[0])
        if op==0x24:return ('const',self.take(1)[0])
        if op in (0x27,0x28,0x2a,0x0b):return ('const',{0x27:True,0x28:False,0x2a:None,0x0b:None}[op])
        if op>=0x60:
            native=((op-0x60)*256+self.take(1)[0]) if op<0x70 else op
            assert native in (114,119,154,216,280,548)
            return ('native',native,self.args())
        raise ValueError('UNEXPECTED_GUARD_OPCODE_'+hex(op))


def _decode(package,original_function,function):
    """独立解码追加区，核对所有上下文长度和分支边界，保留旧函数VM地址。"""
    oldv,olds=struct.unpack_from('<II',original_function,40)
    virtual,stored=struct.unpack_from('<II',function,40)
    oldcode=original_function[48:48+olds];code=function[48:48+stored]
    assert code[:3]==b'\x06'+struct.pack('<H',oldv-1) and code[3:19]==b'\x0b'*16
    assert code[19:19+len(oldcode[11:-1])]==oldcode[11:-1]
    reader=_Reader(package,code,19+len(oldcode[11:-1]),oldv-1);instructions={}
    while reader.pos<len(code):
        pc=reader.vm;op=reader.take(1)[0]
        if op==7:node=('if_not',reader.word(),reader.expr())
        elif op==6:node=('jump',reader.word())
        elif op==0x0f:node=('let',reader.expr(),reader.expr())
        elif op==4:node=('return',reader.expr())
        elif op==0x53:node=('end',)
        else:reader.pos-=1;reader.vm-=1;node=('eval',reader.expr())
        instructions[pc]=(node,reader.vm)
    assert reader.vm==virtual and code[-1]==0x53
    assert all(node[1] in instructions or node[1]==19 for node,nxt in instructions.values() if node[0] in ('jump','if_not'))
    return instructions,oldv-1


def _simulate(package,instructions,entry,netmode,pawn,spot,floor_missing):
    """以解码后的字节执行守卫，记录计时器/Super副作用；不复用生成器的条件实现。"""
    refs={v:k for k,v in package.refs.items()}
    state=dict(NetMode=netmode,Pawn=pawn,StartSpot=None)
    world=dict(NetMode=netmode);game=dict(WorldInfo=world);events=[]
    player=state
    def evaluate(expr,scope=game):
        """仅解释解码后的有限表达式，原生调用只记录副作用，不调用游戏。"""
        tag=expr[0]
        if tag=='const':return expr[1]
        if tag=='byte_to_int':return int(evaluate(expr[1],scope))
        if tag=='ref':
            name=refs[expr[2]].rsplit('.',1)[-1]
            return player if expr[1]==0 and name=='NewPlayer' else scope[name]
        if tag=='context':
            obj=evaluate(expr[1],scope)
            return evaluate(expr[2],obj) if obj is not None else None
        if tag=='call':
            args=[evaluate(x,scope) for x in expr[2]]
            if expr[1]=='FindPlayerStart':events.append(('find_start',));return spot
            if expr[1]=='ClearTimer':assert scope is player;events.append(('clear',*args));return None
            raise ValueError('UNEXPECTED_VIRTUAL_CALL')
        if tag=='super':events.append(('super',));return None
        if tag=='native':
            args=[evaluate(x,scope) for x in expr[2]];op=expr[1]
            if op in (114,154):return args[0]==args[1]
            if op==119:return args[0]!=args[1]
            if op==216:return [a-b for a,b in zip(*args)]
            if op==548:
                assert args[0]==[args[1][0],args[1][1],args[1][2]-256]
                events.append(('trace',));return floor_missing
            if op==280:
                assert scope is player;events.append(('retry',*args));return None
        raise ValueError('UNEXPECTED_EXPRESSION')
    def assign(expr,value,scope=game):
        """只允许写入模拟玩家的StartSpot；其他写入使测试失败。"""
        if expr[0]=='context':return assign(expr[2],value,evaluate(expr[1],scope))
        assert expr[0]=='ref' and refs[expr[2]]=='Engine.Controller.StartSpot'
        scope['StartSpot']=value
    pc=entry
    for _ in range(30):
        node,nxt=instructions[pc];kind=node[0]
        if kind=='if_not':pc=node[1] if not evaluate(node[2]) else nxt
        elif kind=='jump':
            assert node[1]==19;return events
        elif kind=='let':assign(node[1],evaluate(node[2]));pc=nxt
        elif kind=='eval':evaluate(node[1]);pc=nxt
        elif kind=='return':return events
        else:raise ValueError('UNEXPECTED_END_WITHOUT_SUPER_OR_WAIT')
    raise ValueError('GUARD_LOOP')


class HuntFingerprintTests(unittest.TestCase):
    """不需要真实客户端夹具的拒绝测试。"""

    def test_unknown_source_rejected_before_parse_or_install(self):
        """未知版本、错误类型和截断文件在解码前失败，不生成任意包变体。"""
        for blob in (None,'unknown',b'',b'unknown',b'\0'*4096):
            with self.subTest(kind=type(blob).__name__),self.assertRaisesRegex(ValueError,'FINGERPRINT'):
                hunt.fix_standalone_spawn_floor(blob)


class HuntCompatTests(unittest.TestCase):
    """真实固定包仅在临时目录使用，测试不启动游戏、不写数据库或原客户端。"""

    @classmethod
    def setUpClass(cls):
        """读取本地归档夹具；缺失时明确跳过，不能声称真实包通过。"""
        path=Path(__file__).resolve().parents[1]/'runtime/native-recovery/coherent-2020/nz'/hunt.RELATIVE
        if not path.is_file():raise unittest.SkipTest('固定本地猎场夹具缺失')
        cls.source=path.read_bytes();cls.before=hunt._Package(cls.source)
        cls.variant=hunt.fix_standalone_spawn_floor(cls.source);cls.after=hunt._Package(cls.variant)
        cls.old_record,cls.old_fn=cls.before.export('TGADBaseGame.RestartPlayer')
        cls.new_record,cls.new_fn=cls.after.export('TGADBaseGame.RestartPlayer')
        cls.instructions,cls.entry=_decode(cls.after,cls.old_fn,cls.new_fn)

    def test_fixed_live_candidate_and_unchanged_original_body(self):
        """产物必须与两次实机的摘要相同，旧函数、目录及非目标导出均保持。"""
        self.assertEqual(hashlib.sha256(self.source).hexdigest(),hunt.SOURCE_SHA256)
        self.assertEqual(hashlib.sha256(self.variant).hexdigest(),hunt.CANDIDATE_SHA256)
        offset,size=self.old_record['offset'],self.old_record['size']
        self.assertEqual(self.variant[offset:offset+size],self.old_fn)
        self.assertEqual(self.source[-128:],self.variant[-128:])
        self.assertEqual(self.before.refs,self.after.refs)
        self.assertEqual(self.before.exports[:4257],self.after.exports[:4257])
        self.assertEqual(self.before.exports[4258:],self.after.exports[4258:])
        expected=bytearray(self.source[:-128])
        struct.pack_into('<II',expected,self.old_record['record']+32,len(self.new_fn),len(self.source)-128)
        self.assertEqual(self.variant[:len(expected)],expected)
        self.assertEqual(struct.unpack_from('<II',self.new_fn,40),(600,420))

    def test_numbered_objects_have_distinct_identity(self):
        """原包中同名、不同FName数字的导入项合法，不应误判为重复身份。"""
        self.assertNotEqual(self.before.refs['FamilyEvaPig_Body_001.Mesh.SK_Body_2'],
                            self.before.refs['FamilyEvaPig_Body_001.Mesh.SK_Body'])

    def test_online_and_existing_pawn_call_only_original_super(self):
        """客户端、监听、专服及已有Pawn都不检查地板，不增加延迟或修改原生成路径。"""
        spot={'Location':[100.,200.,50.]}
        for mode,pawn in ((1,None),(2,None),(3,None),(0,{'pawn':1})):
            with self.subTest(mode=mode,pawn=pawn):
                self.assertEqual(_simulate(self.after,self.instructions,self.entry,mode,pawn,spot,True),[('super',)])

    def test_missing_floor_waits_without_spawning(self):
        """地板尚未出现时只安排原生0.25秒重试，不提前生成或伪造碰撞就绪。"""
        events=_simulate(self.after,self.instructions,self.entry,0,None,{'Location':[100.,200.,50.]},True)
        self.assertEqual(events,[('find_start',),('trace',),('retry',.25,True,'ServerRestartPlayer')])

    def test_ready_floor_clears_retry_before_original_spawn(self):
        """原生碰撞命中后先清重试计时器，再执行原Super及原函数余下部分。"""
        events=_simulate(self.after,self.instructions,self.entry,0,None,{'Location':[100.,200.,50.]},False)
        self.assertEqual(events,[('find_start',),('trace',),('clear','ServerRestartPlayer'),('super',)])

    def test_missing_spawn_preserves_original_failure(self):
        """没有出生点时不凭空传送或无限等待地板，沿用原Super的失败处理。"""
        events=_simulate(self.after,self.instructions,self.entry,0,None,None,True)
        self.assertEqual(events,[('find_start',),('clear','ServerRestartPlayer'),('super',)])

    def test_interrupted_install_restores_and_rejects_double_patch(self):
        """新守卫实例可恢复原包；候选再次作为原件时拒绝，不累加修改。"""
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)/'client';out=Path(directory)/'out';path=root/hunt.RELATIVE
            path.parent.mkdir(parents=True);out.mkdir();path.write_bytes(self.source)
            guard=NativeConfigGuard(root,out);receipt=hunt.install_candidate(guard)
            self.assertEqual(receipt['variant_sha256'],hunt.CANDIDATE_SHA256)
            self.assertFalse(receipt['playable_map_accepted'])
            with self.assertRaisesRegex(ValueError,'SOURCE_FINGERPRINT'):
                hunt.install_candidate(guard)
            restored=NativeConfigGuard.from_plan(root,out).restore()
            self.assertTrue(restored['configuration_restored']);self.assertEqual(path.read_bytes(),self.source)
