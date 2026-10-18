"""单机本人装备候选的独立VM、原包保留和持久恢复测试；不替代实机或多人证明。"""
import hashlib
from pathlib import Path
import struct
import tempfile
import unittest

from tools import native_owned_loadout_compat as owned
from tools.native_config_guard import NativeConfigGuard, content_limit
from .test_native_hunt_compat import _Reader as HuntReader


class Reader(HuntReader):
    """独立读取额外静态调用、类型转换与日志表达式，拒绝任何未使用的指令。"""
    def expr(self):
        """按UE3序列化宽度核对每个引用及Context跳过量，不调用生成器辅助函数。"""
        op=self.data[self.pos]
        if op in (0x1d,0x25,0x2c,0x2d):
            self.take(1)
            if op==0x1d:return ('const',struct.unpack('<i',self.take(4))[0])
            if op==0x25:return ('const',0)
            if op==0x2c:return ('const',self.take(1)[0])
            return ('bool_ref',self.expr())
        if op in (0x12,0x2e,0x20,0x1f,0x4a) or op>=0x60:
            self.take(1)
            if op==0x12:
                obj=self.expr();skip=self.word();prop=self.number();assert self.take(1)==b'\0'
                before=self.vm;inner=self.expr();assert self.vm-before==skip
                return ('classcontext',obj,inner,prop)
            if op==0x2e:
                cl=self.number();return ('cast',cl,self.expr())
            if op==0x20:
                return ('object',self.number())
            if op==0x1f:
                value=[]
                while True:
                    c=self.take(1)
                    if c==b'\0':break
                    value.append(c)
                return ('const',b''.join(value).decode('ascii'))
            if op==0x4a:return ('const',None)
            native=((op-0x60)*256+self.take(1)[0]) if op<0x70 else op
            assert native in (112,114,119,150,151,154,231)
            return ('native',native,self.args())
        return super().expr()


def decode(package, original, modified, restart=False):
    """分别独立核对首日志/赋值的VM宽度、保留原体和全部新分支，文件偏移不能当VM地址。"""
    ov,os=struct.unpack_from('<II',original,40);nv,ns=struct.unpack_from('<II',modified,40)
    old=original[48:48+os];new=modified[48:48+ns]
    preserve,first_end,first_vm=(0,16,28) if restart else (5,87,119)
    first=Reader(package,old,preserve,preserve)
    if restart:
        assert first.take(1)==b'\x0f';left=first.expr();right=first.expr()
        assert left[0]=='ref' and right[0]=='cast' and package.refs['TGPlayerController']==right[1]
    else:
        node=first.expr();assert node[0]=='native' and node[1]==231
    assert first.pos==first_end and first.vm==first_vm
    assert new[:preserve]==old[:preserve] and new[preserve:preserve+3]==b'\x06'+struct.pack('<H',ov-1)
    assert new[preserve+3:first_vm]==b'\x0b'*(first_vm-preserve-3)
    assert new[first_vm:first_vm+len(old[first_end:-1])]==old[first_end:-1]
    reader=Reader(package,new,first_vm+len(old[first_end:-1]),ov-1);instructions={}
    while reader.pos<len(new):
        pc=reader.vm;op=reader.take(1)[0]
        if op==7:node=('if_not',reader.word(),reader.expr())
        elif op==6:node=('jump',reader.word())
        elif op==4:node=('return',reader.expr())
        elif op in (0x0f,0x14):node=('let',reader.expr(),reader.expr())
        elif op==0x53:node=('end',)
        else:
            reader.pos-=1;reader.vm-=1;node=('eval',reader.expr())
        instructions[pc]=(node,reader.vm)
    assert reader.vm==nv
    assert all(node[1] in instructions or node[1]==first_vm for node,_ in instructions.values() if node[0] in ('if_not','jump'))
    return instructions,ov-1


def simulate(package, instructions, start, netmode, human=True, pawn=True, pri=True,
             store_owned=True,store_hours=876000,store_level=3,room_controller=True,room_data=True,capture=None):
    """执行独立解码树，核对在线/机器人不读取本地库存，人类只调用真实角色和两条原入口。"""
    refs={v:k for k,v in package.refs.items()};events=[]
    world={'NetMode':netmode};rep={'CharInfo':{},'bStoreSysOpen_PVE':True,'StoreSysLevel_PVE':2} if pri else None
    player={'PlayerReplicationInfo':rep,'human':human} if pawn else None
    controller={'PlayerReplicationInfo':rep,'local':human}
    scope={'WorldInfo':world}

    def evaluate(expr, obj=scope):
        """只解释候选实际所需操作，记录而不执行任何游戏函数或资产接口。"""
        kind=expr[0]
        if kind=='const':return expr[1]
        if kind=='ref':
            if expr[1]==0:return controller if refs[expr[2]].endswith('.NewPlayer') else player
            return obj[refs[expr[2]].rsplit('.',1)[-1]]
        if kind=='context':
            target=evaluate(expr[1],obj)
            return evaluate(expr[2],target) if target is not None else None
        if kind=='cast':
            label=refs[expr[1]];assert label in ('TGPlayerReplicationInfo','TGPlayerController','TGAvatarRoomController','TGAvatarRoomData')
            return evaluate(expr[2],obj) if label=='TGPlayerReplicationInfo' or human else None
        if kind=='bool_ref':return evaluate(expr[1],obj)
        if kind=='byte_to_int':return int(evaluate(expr[1],obj))
        if kind=='object':return refs[expr[1]]
        if kind=='classcontext':
            assert evaluate(expr[1],obj) in ('TGAvatarChar_Data','TGEngine');return evaluate(expr[2],obj)
        if kind=='super':
            identity=refs[expr[1]];args=[evaluate(v,obj) for v in expr[2]]
            if identity=='TGAvatarChar_Data.GetCharInfo':
                assert args[0] is rep['CharInfo'] and args[1] is True
                events.append('GetCharInfo');return None
            if identity in ('TGEngine.GetTGControlSystem','TGEngine.GetTGGameDataSystem'):return {'system':identity}
            if identity=='TGControlSystem.GetController':
                assert args==['TGAvatarRoomController'];return {'controller':True} if room_controller else None
            if identity=='TGDataSystem.GetGameData':
                assert args==['TGAvatarRoomData'];return {'CurPVEStoreGrowLevel':store_level} if room_data else None
            assert args==[101296]
            if identity=='TGAvatarRoomController.bOpenGrowStore':return store_owned
            assert identity=='TGAvatarRoomController.GetGrowStoreAvailTime';return store_hours
        if kind=='call':
            name=expr[1];args=[evaluate(v,obj) for v in expr[2]]
            if name=='IsHumanControlled':return obj['human']
            if name=='IsLocalPlayerController':return obj['local']
            assert name in ('AddFunctionalItemFor','AddInventoryFor') and args[0] is player
            if name=='AddFunctionalItemFor':assert args[1] is rep
            events.append(name);return None
        if kind=='native':
            args=[evaluate(v,obj) for v in expr[2]]
            if expr[1] in (114,154):return args[0]==args[1]
            if expr[1]==119:return args[0]!=args[1]
            if expr[1]==150:return args[0]<args[1]
            if expr[1]==151:return args[0]>args[1]
            # 完整原日志中的Controller/Uin不模拟；遇到它只证明已经走回原生分支。
            if expr[1]==231:events.append('original');return None
        raise ValueError('UNEXPECTED_VM_EXPRESSION')

    def assign(expr,value,obj=scope):
        """只模拟两条百宝箱字段；原RestartPlayer第一条PC赋值仍作为原流程边界。"""
        if expr[0]=='context':return assign(expr[2],value,evaluate(expr[1],obj))
        if expr[0]=='bool_ref':return assign(expr[1],value,obj)
        assert expr[0]=='ref';key=refs[expr[2]]
        if key=='TGGame.RestartPlayer.PC':return False
        assert key in ('TGPlayerReplicationInfo.bStoreSysOpen_PVE','TGPlayerReplicationInfo.StoreSysLevel_PVE')
        assert obj is rep;obj[key.rsplit('.',1)[-1]]=value
        if capture is not None:capture.update(active=rep['bStoreSysOpen_PVE'],level=rep['StoreSysLevel_PVE'])
        return True

    pc=start
    for _ in range(64):
        node,nxt=instructions[pc];kind=node[0]
        # 原日志作为完整原路径的首指令，避免模拟不相关原游戏字段。
        if kind=='eval' and node[1][0]=='native' and node[1][1]==231:
            return events+['original']
        if kind=='let':
            if not assign(node[1],evaluate(node[2])):return events+['original']
            pc=nxt;continue
        if kind=='if_not':pc=node[1] if not evaluate(node[2]) else nxt
        elif kind=='eval':evaluate(node[1]);pc=nxt
        elif kind=='return':return events
        elif kind=='jump':assert node[1] in (28,119);return events+['original']
        else:raise ValueError('UNEXPECTED_GUARD_END')
    raise ValueError('GUARD_LOOP')


class OwnedFingerprintTests(unittest.TestCase):
    """不依赖游戏文件的拒绝与恢复边界用例。"""
    def test_foreign_source_rejected(self):
        """任意内容不能作为当前构建生成或安装，不能通过猜测偏移修补。"""
        with self.assertRaisesRegex(ValueError,'SOURCE_FINGERPRINT'):
            owned.fix_standalone_owned_loadout(b'wrong build')

    def test_ut_limit_and_restart_restore(self):
        """只有准确UTGame路径支持32MiB，中断后原件可恢复，其他配置继续限制4MiB。"""
        self.assertEqual(content_limit(owned.RELATIVE),32*1024*1024)
        self.assertEqual(content_limit('TGame/CookedPC/Other.u'),4*1024*1024)
        with tempfile.TemporaryDirectory() as d:
            base=Path(d);root=base/'client';out=base/'evidence';root.mkdir();out.mkdir()
            p=root/owned.RELATIVE;p.parent.mkdir(parents=True);old=b'a'*(5*1024*1024);p.write_bytes(old)
            guard=NativeConfigGuard(root,out);guard.apply(owned.RELATIVE,b'b'*len(old))
            self.assertTrue(NativeConfigGuard.from_plan(root,out).restore()['configuration_restored'])
            self.assertEqual(p.read_bytes(),old)
            with self.assertRaises(ValueError):guard.apply('other.ini',old)


class OwnedLoadoutTests(unittest.TestCase):
    """固定真实原包验证，原件缺失时明确跳过，不能当实机或可玩验收。"""
    @classmethod
    def setUpClass(cls):
        """只读原包，候选留在内存；不安装工作副本，不启动游戏进程。"""
        path=Path(__file__).resolve().parents[1]/'runtime/native-recovery/coherent-2020/nz'/owned.RELATIVE
        if not path.is_file():raise unittest.SkipTest('固定原包不可用')
        cls.raw=path.read_bytes();cls.old=owned._Package(cls.raw)
        cls.variant=owned.fix_standalone_owned_loadout(cls.raw);cls.new=owned._Package(cls.variant)
        cls.old_record,cls.function=cls.old.export('TGGame.AddDefaultInventoryEx')
        cls.new_record,cls.modified=cls.new.export('TGGame.AddDefaultInventoryEx')
        cls.restart_record,cls.restart_function=cls.old.export('TGGame.RestartPlayer')
        cls.new_restart_record,cls.modified_restart=cls.new.export('TGGame.RestartPlayer')

    def test_independent_vm_and_online_bot_isolation(self):
        """四种NetMode、机器人、空Pawn/PRI分别核对，只有单机人类读取本人数据。"""
        instructions,start=decode(self.old,self.function,self.modified)
        self.assertEqual(simulate(self.old,instructions,start,0),['AddInventoryFor'])
        for mode in (1,2,3):
            with self.subTest(mode=mode):self.assertEqual(simulate(self.old,instructions,start,mode),['original'])
        for state in (dict(human=False),dict(pawn=False),dict(pri=False)):
            with self.subTest(state=state):self.assertEqual(simulate(self.old,instructions,start,0,**state),['original'])

    def test_character_read_precedes_original_restart_and_card_initialization(self):
        """真实角色数据在原RestartPlayer前读取，卡片初始化只由原函数执行一次，不重置HasGetTools。"""
        instructions,start=decode(self.old,self.restart_function,self.modified_restart,True)
        self.assertEqual(simulate(self.old,instructions,start,0),['GetCharInfo','original'])
        for mode in (1,2,3):
            with self.subTest(mode=mode):self.assertEqual(simulate(self.old,instructions,start,mode),['original'])
        for state in (dict(human=False),dict(pri=False)):
            with self.subTest(state=state):self.assertEqual(simulate(self.old,instructions,start,0,**state),['original'])

    def test_store_uses_owned_unexpired_real_level_and_never_debug_fixed_three(self):
        """真实1/2/3级分别同步；过期、缺物品、损坏等级或缺控制器重置为关闭0级，在线不写。"""
        instructions,start=decode(self.old,self.restart_function,self.modified_restart,True)
        for value in (1,2,3):
            captured={};simulate(self.old,instructions,start,0,store_level=value,capture=captured)
            self.assertEqual(captured,dict(active=True,level=value))
        for values in (dict(store_owned=False),dict(store_hours=0),dict(store_hours=-1),
                       dict(store_level=0),dict(store_level=4),dict(store_level=-1),
                       dict(room_controller=False),dict(room_data=False)):
            with self.subTest(values=values):
                captured={};simulate(self.old,instructions,start,0,capture=captured,**values)
                self.assertEqual(captured,dict(active=False,level=0))
        for mode in (1,2,3):
            captured={};simulate(self.old,instructions,start,mode,capture=captured)
            self.assertEqual(captured,{})

    def test_original_package_retained_except_export_pointer(self):
        """按逻辑块独立比较全部原内容，原区仅两条函数导出指针变化，其余内容逐字节保留。"""
        self.assertEqual(hashlib.sha256(self.variant).hexdigest(),owned.CANDIDATE_SHA256)
        self.assertEqual(self.new.refs,self.old.refs)
        patches=[(self.restart_record['record']+32,struct.pack('<II',len(self.modified_restart),self.old.logical_end)),
                 (self.old_record['record']+32,struct.pack('<II',len(self.modified),self.old.logical_end+len(self.modified_restart)))]
        for i,(_,offset,length,_,_,_) in enumerate(self.old.chunks):
            expected=bytearray(self.old.clear(i))
            for patch,pointer in patches:
                if offset<=patch<offset+length:expected[patch-offset:patch-offset+8]=pointer
            actual=self.new.clear(i)
            self.assertEqual(actual[:length],expected)
            self.assertEqual(len(actual)-length,len(self.modified)+len(self.modified_restart) if i==77 else 0)
        for a,b in zip(self.old.exports,self.new.exports):
            if a['index'] not in (self.old_record['index'],self.restart_record['index']):self.assertEqual(a,b)
        self.assertEqual(self.new_restart_record['offset'],self.old.logical_end)
        self.assertEqual(self.new_record['offset'],self.old.logical_end+len(self.modified_restart))

    def test_second_patch_rejected(self):
        """已变体包不能再次追加，避免同一启动重复安装或错认来源。"""
        with self.assertRaisesRegex(ValueError,'SOURCE_FINGERPRINT'):
            owned.fix_standalone_owned_loadout(self.variant)
