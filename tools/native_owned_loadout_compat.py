"""当前构建单机装备读取候选；只从本人原生库存建立角色数据，不生成武器或资产权益。

原TGGame.RestartPlayer在Standalone始终调用调试DefaultInventory。本候选在本地玩家出生前
读取已登录客户端GetCharInfo，原首次功能卡初始化之后按实际模式分派AddInventoryFor。
百宝箱按本人有效物品和大厅真实等级同步，不使用原调试EnablePlayerVIP固定3级。
在线和机器人继续完整原函数，原VM分支地址不移动。候选不在默认Launcher启用，须限时实机验收。
"""
from collections import OrderedDict
import hashlib
import io
import struct

from tools.native_package_dependencies import Cipher, algorithms, modes, SCRIPT_RESOURCE_KEY, decompress_chunk
from tools.native_hunt_compat import _Expr, _reference, _field, _context, _native

PROFILE='cn2020-standalone-owned-loadout-v3-candidate'
RELATIVE='TGame/CookedPC/UTGame.u'
SOURCE_SHA256='77b80ac0bc199fa6bfb0d3a2d63f938a90bb75153878b887200c363b6931ba56'
FUNCTION_SHA256='e77c40b3fd4dfc2725311d4e8bae3d0fd9e4987626a429f65ea61cac84a5353e'
CANDIDATE_SHA256='68f62aa1b126f66f7e96c1f867a1bb8d5bfba2baf9c38e7762d9f2374c81cfae'
RESTART_SHA256='20624afe8cb6c208730bff98e8cc04c56ee1daffdfe9659d562f4b8ba1c25fbd'


def _require(value, code):
    """损坏或版本变化直接拒绝，优化模式下也保留检查，不提供跳过摘要的入口。"""
    if not value:
        raise ValueError('OWNED_LOADOUT_'+code)


class _Package:
    """仅解析当前UTGame的固定压缩目录、对象身份与有限函数，缓存最多两个有界块。"""

    def __init__(self, raw):
        """回读候选复用相同布局验证；公开生成入口另外固定完整原件SHA256。"""
        _require(isinstance(raw,bytes) and 20*1024*1024<len(raw)<32*1024*1024,'SIZE_INVALID')
        self.raw=raw;tag,self.end=struct.unpack_from('<II',raw)
        _require((tag,self.end)==(0x3e27a900,1373),'HEADER_INVALID')
        self.footer_size=1376
        _require(raw[8:self.end]==raw[-1376:][:self.end-8],'DIRECTORY_MIRROR_INVALID')
        dec=Cipher(algorithms.AES(SCRIPT_RESOURCE_KEY),modes.ECB()).decryptor()
        self.header=dec.update(raw[-1376:])+dec.finalize()
        _require(struct.unpack_from('<I',self.header)[0]==0x202fc and struct.unpack_from('<i',self.header,8)[0]==5,'SUMMARY_INVALID')
        nc,no,ec,eo,ic,ioff=struct.unpack_from('<IIIIII',self.header,21)
        _require((nc,no,ec,eo,ic,ioff)==(63550,125,149875,2253971,3971,2142783),'TABLE_LAYOUT_INVALID')
        _require(struct.unpack_from('<II',self.header,105)==(2,78),'COMPRESSION_LAYOUT_INVALID')
        _require(self.header[1361:]==bytes.fromhex('1035635d')+bytes(11),'TAIL_INVALID')
        self.cache=OrderedDict();self.chunks=[]
        physical_end=self.end;logical_end=None
        for i in range(78):
            desc=113+i*16;logical,size,physical,stored=struct.unpack_from('<IIII',self.header,desc)
            _require(physical==physical_end and (logical_end is None or logical==logical_end)
                     and 0<size<=16*1024*1024 and physical+stored<=len(raw)-1376,'CHUNK_BOUNDS_INVALID')
            magic,block,compressed,total=struct.unpack_from('<IIII',raw,physical)
            _require(magic==0x9e2a83c1 and 0<block<=131072 and total==size
                     and stored==16+8*((total+block-1)//block)+compressed,'CHUNK_HEADER_INVALID')
            self.chunks.append((desc,logical,size,physical,stored,block))
            physical_end=physical+stored;logical_end=logical+size
        _require(physical_end==len(raw)-1376,'PHYSICAL_END_INVALID')
        self.logical_end=logical_end
        names=self.read(no,ioff-no);self.names=[];position=0
        for _ in range(nc):
            length=struct.unpack_from('<i',names,position)[0];position+=4
            width=2 if length<0 else 1;size=abs(length)*width
            _require(0<abs(length)<=2048 and position+size+8<=len(names),'NAME_BOUNDS_INVALID')
            value=names[position:position+size]
            _require(value[-width:]==bytes(width),'NAME_TERMINATOR_INVALID')
            self.names.append(value[:-width].decode('utf-16le' if width==2 else 'utf-8'));position+=size+8
        _require(position==len(names),'NAME_TABLE_INVALID')
        imports=self.read(ioff,ic*28)
        self.imports=[struct.unpack_from('<iiiiiii',imports,i*28) for i in range(ic)]
        self.exports=[];position=eo
        for i in range(ec):
            record=self.read(position,68)
            cl,parent,outer,name,number=struct.unpack_from('<iiiii',record)
            size,offset,_,count=struct.unpack_from('<iiii',record,32)
            _require(0<=name<nc and number>=0 and 0<=count<10000 and size>=0
                     and no<=offset<=self.logical_end-size,'EXPORT_BOUNDS_INVALID')
            self.exports.append(dict(index=i+1,cl=cl,parent=parent,outer=outer,name=self.names[name],number=number,
                                     size=size,offset=offset,record=position))
            position+=68+count*4
        self.refs={}
        for index in range(-ic,ec+1):
            if index:
                name=self.identity(index)
                _require(name not in self.refs,'DUPLICATE_IDENTITY')
                self.refs[name]=index

    def clear(self, index):
        """只解压当前访问块；只读检查大包时不把整份逻辑包长期缓存于内存。"""
        if index not in self.cache:
            _,_,size,physical,stored,_=self.chunks[index]
            self.cache[index]=decompress_chunk(io.BytesIO(self.raw),physical,stored,size,len(self.raw))
            while len(self.cache)>2:
                self.cache.popitem(last=False)
        self.cache.move_to_end(index)
        return self.cache[index]

    def read(self, offset, size):
        """读取有限连续逻辑区间；拒绝洞、重叠和越界，不能以短读拼出似是而非的函数。"""
        _require(0<size<=12*1024*1024 and 0<=offset<=self.logical_end-size,'READ_BOUNDS_INVALID')
        cursor=offset;pieces=[]
        for i,(_,logical,length,_,_,_) in enumerate(self.chunks):
            start=max(logical,offset);stop=min(logical+length,offset+size)
            if start>=stop:
                continue
            _require(start==cursor,'READ_GAP')
            pieces.append(self.clear(i)[start-logical:stop-logical]);cursor=stop
        _require(cursor==offset+size,'READ_INCOMPLETE')
        return b''.join(pieces)

    def identity(self, index, depth=0):
        """按真实Outer和FName数字解析完整身份；类引用、属性与函数不能仅靠短名匹配。"""
        _require(depth<64 and index!=0,'REFERENCE_INVALID')
        if index<0:
            _require(-index<=len(self.imports),'IMPORT_REFERENCE_INVALID')
            row=self.imports[-index-1];outer,ni,num=row[4:7]
            _require(0<=ni<len(self.names) and num>=0,'IMPORT_NAME_INVALID')
            name=self.names[ni]
        else:
            _require(index<=len(self.exports),'EXPORT_REFERENCE_INVALID')
            row=self.exports[index-1];outer,name,num=row['outer'],row['name'],row['number']
        if num:
            name+='_'+str(num-1)
        return (self.identity(outer,depth+1)+'.' if outer else '')+name

    def export(self, identity):
        """只读完整身份指定的导出，不接受失效索引和导入对象冒充可编辑函数。"""
        index=self.refs.get(identity,0);_require(index>0,'FUNCTION_MISSING')
        record=self.exports[index-1]
        return record,self.read(record['offset'],record['size'])

    def repack(self, patches, addition):
        """只重压导出记录和末块，镜像目录同时更新；其余块及所有原导出逻辑地址保持。"""
        import lzokay
        result=bytearray(self.raw[:self.end]);header=bytearray(self.header)
        for index,(desc,logical,size,physical,stored,block) in enumerate(self.chunks):
            changes=[(at,value) for at,value in patches if logical<=at and at+len(value)<=logical+size]
            extra=addition if index==len(self.chunks)-1 else b''
            chunk=self.raw[physical:physical+stored]
            if changes or extra:
                clear=bytearray(self.clear(index))
                for at,value in changes:
                    clear[at-logical:at-logical+len(value)]=value
                clear.extend(extra)
                pieces=[lzokay.compress(bytes(clear[i:i+block])) for i in range(0,len(clear),block)]
                chunk=struct.pack('<IIII',0x9e2a83c1,block,sum(map(len,pieces)),len(clear))
                chunk+=b''.join(struct.pack('<II',len(p),min(block,len(clear)-i*block)) for i,p in enumerate(pieces))+b''.join(pieces)
                _require(decompress_chunk(io.BytesIO(chunk),0,len(chunk),len(clear),len(chunk))==clear,'ROUNDTRIP_FAILED')
            struct.pack_into('<IIII',header,desc,logical,size+len(extra),len(result),len(chunk))
            result.extend(chunk)
        enc=Cipher(algorithms.AES(SCRIPT_RESOURCE_KEY),modes.ECB()).encryptor()
        encrypted=enc.update(bytes(header))+enc.finalize()
        result[8:self.end]=encrypted[:self.end-8];result.extend(encrypted)
        return bytes(result)


def _rewritten_functions(package):
    """固定两条函数：出生前读取角色一次，默认装备入口只分派实际模式，不二次初始化功能卡。"""
    refs=package.refs

    def prop(obj, identity):
        """只取当前包已核验属性，Context同时编码真实返回类型及VM跳过长度。"""
        index=refs[identity]
        return _context(obj,_field(index),index)

    def named(name, *args):
        """调用原本存在的虚函数；不追加名称、导入项或角色字段。"""
        expr=_Expr(b'\x1b'+struct.pack('<II',package.names.index(name),0))
        for arg in args:
            expr=expr+arg
        return expr+_Expr(b'\x16')

    def final(identity,*args):
        """引用既有静态/原生函数身份，参数类型和返回上下文沿原包，不追加入口。"""
        expr=_Expr(b'\x1c')+_reference(refs[identity])
        for arg in args:
            expr=expr+arg
        return expr+_Expr(b'\x16')

    def static(cl,identity,*args):
        """使用原UE3 ClassContext，真实VM跳过量包含全部展开后的对象引用。"""
        inner=final(identity,*args)
        return (_Expr(b'\x12\x20')+_reference(refs[cl])+_Expr(struct.pack('<H',inner.virtual))
                +_reference(0)+_Expr(b'\0')+inner)

    def name(value):
        """FName常量必须已在原名称表，禁止构造新的数据系统或控制器。"""
        return _Expr(b'\x21'+struct.pack('<II',package.names.index(value),0))

    def bool_prop(obj,identity):
        """布尔实例字段使用原EnablePlayerVIP同样的BoolVariable包装，避免破坏位域。"""
        index=refs[identity]
        return _context(obj,_Expr(b'\x2d')+_field(index),index)

    standalone=_native(154,_Expr(b'\x38\x3a')+prop(_field(refs['Engine.Actor.WorldInfo']),'Engine.WorldInfo.NetMode'),_Expr(b'\x38\x3a\x24\0'))
    output=[]
    for identity,fingerprint,layout,preserve,first_end,first_vm in (
        ('TGGame.RestartPlayer',RESTART_SHA256,(1865,1341,1404),0,16,28),
        ('TGGame.AddDefaultInventoryEx',FUNCTION_SHA256,(763,507,570),5,87,119)):
        record,function=package.export(identity)
        _require(hashlib.sha256(function).hexdigest()==fingerprint,'FUNCTION_FINGERPRINT_MISMATCH')
        virtual,stored=struct.unpack_from('<II',function,40);script=function[48:48+stored]
        _require((virtual,stored,len(function))==layout and script[-3:]==b'\x04\x0b\x53','FUNCTION_LAYOUT_INVALID')
        parts=[];fallbacks=[];cursor=virtual-1

        def emit(expr):
            """累计序列化和VM两套偏移，引用展开不能作为普通4字节处理。"""
            nonlocal cursor
            parts.append(expr);cursor+=expr.virtual

        def guard(condition):
            """条件不成立即回到该函数原首指令；分支地址一次性回填。"""
            fallbacks.append(len(parts));emit(_Expr(b'\x07\0\0')+condition)

        guard(standalone)
        if identity=='TGGame.RestartPlayer':
            _require(script[:16]==bytes.fromhex('0f00114d00002ee41a000000134d0000'),'RESTART_PREFIX_INVALID')
            player=_field(refs['TGGame.RestartPlayer.NewPlayer'],True)
            controller=_Expr(b'\x2e')+_reference(refs['TGPlayerController'])+player
            guard(_native(119,controller,_Expr(b'\x2a')))
            guard(_context(controller,named('IsLocalPlayerController')))
            pri=_Expr(b'\x2e')+_reference(refs['TGPlayerReplicationInfo'])+prop(controller,'Engine.Controller.PlayerReplicationInfo')
            guard(_native(119,pri,_Expr(b'\x2a')))
            info=prop(pri,'TGPlayerReplicationInfo.CharInfo')
            get_char=_Expr(b'\x1c')+_reference(refs['TGAvatarChar_Data.GetCharInfo'])+info+_Expr(b'\x27\x16')
            get_char=_Expr(b'\x12\x20')+_reference(refs['TGAvatarChar_Data'])+_Expr(struct.pack('<H',get_char.virtual))+_reference(0)+_Expr(b'\0')+get_char
            emit(get_char)
            # 原单机未经过在线DS.InitPRI；只补本人百宝箱状态，不调用固定3级的调试命令。
            active=bool_prop(pri,'TGPlayerReplicationInfo.bStoreSysOpen_PVE')
            store_level=prop(pri,'TGPlayerReplicationInfo.StoreSysLevel_PVE')
            emit(_Expr(b'\x14')+active+_Expr(b'\x28'))
            emit(_Expr(b'\x0f')+store_level+_Expr(b'\x25'))
            control_system=static('TGEngine','TGEngine.GetTGControlSystem')
            controller_data=_context(control_system,final('TGControlSystem.GetController',name('TGAvatarRoomController')))
            room_controller=_Expr(b'\x2e')+_reference(refs['TGAvatarRoomController'])+controller_data
            game_data=static('TGEngine','TGEngine.GetTGGameDataSystem')
            room_data=_Expr(b'\x2e')+_reference(refs['TGAvatarRoomData'])+_context(game_data,final('TGDataSystem.GetGameData',name('TGAvatarRoomData')))
            guard(_native(119,room_controller,_Expr(b'\x2a')))
            guard(_native(119,room_data,_Expr(b'\x2a')))
            box_id=_Expr(b'\x1d'+struct.pack('<i',101296))
            guard(_context(room_controller,final('TGAvatarRoomController.bOpenGrowStore',box_id)))
            # 权益存在和剩余时间分别检查，过期当秒不能把旧大厅等级带入新Pawn。
            hours=_context(room_controller,final('TGAvatarRoomController.GetGrowStoreAvailTime',box_id))
            guard(_native(151,hours,_Expr(b'\x25')))
            real_level=prop(room_data,'TGAvatarRoomData.CurPVEStoreGrowLevel')
            guard(_native(151,real_level,_Expr(b'\x25')))
            guard(_native(150,real_level,_Expr(b'\x2c\x04')))
            emit(_Expr(b'\x0f')+store_level+real_level)
            emit(_Expr(b'\x14')+active+_Expr(b'\x27'))
            # 继续原RestartPlayer，真实功能卡首次初始化和角色/武器生成都发生在读取之后。
        else:
            _require(script[:5]==bytes.fromhex('4902002515') and script[84:87]==bytes.fromhex('164a16'),'INVENTORY_PREFIX_INVALID')
            pawn=_field(refs['TGGame.AddDefaultInventoryEx.PlayerPawn'],True)
            guard(_native(119,pawn,_Expr(b'\x2a')))
            guard(_context(pawn,named('IsHumanControlled')))
            pri=_Expr(b'\x2e')+_reference(refs['TGPlayerReplicationInfo'])+prop(pawn,'Engine.Pawn.PlayerReplicationInfo')
            guard(_native(119,pri,_Expr(b'\x2a')))
            emit(named('AddInventoryFor',pawn))
            emit(_Expr(b'\x04\x0b'))
        fallback=cursor
        emit(_Expr(script[preserve:first_end],first_vm-preserve))
        emit(_Expr(b'\x06'+struct.pack('<H',first_vm)))
        emit(_Expr(b'\x53'))
        _require(cursor<65536,'VM_LIMIT')
        for index in fallbacks:
            expr=parts[index];parts[index]=_Expr(expr.data[:1]+struct.pack('<H',fallback)+expr.data[3:],expr.virtual)
        addition=b''.join(expr.data for expr in parts)
        code=script[:preserve]+b'\x06'+struct.pack('<H',virtual-1)+b'\x0b'*(first_vm-preserve-3)+script[first_end:-1]+addition
        function_new=bytearray(function[:48]+code+function[48+stored:])
        struct.pack_into('<II',function_new,40,cursor,len(code))
        output.append((identity,record,bytes(function_new)))
    return output


def fix_standalone_owned_loadout(original):
    """精确原包上生成两函数候选，其他原内容和在线逻辑保持；不安装、不调用资产接口。"""
    _require(isinstance(original,bytes) and hashlib.sha256(original).hexdigest()==SOURCE_SHA256,'SOURCE_FINGERPRINT_MISMATCH')
    package=_Package(original);functions=_rewritten_functions(package);patches=[];addition=b''
    for identity,record,modified in functions:
        patches.append((record['record']+32,struct.pack('<II',len(modified),package.logical_end+len(addition))))
        addition+=modified
    result=package.repack(patches,addition)
    verified=_Package(result);_require(verified.refs==package.refs,'REFERENCE_ROUNDTRIP_FAILED')
    offset=package.logical_end
    for identity,record,modified in functions:
        updated,body=verified.export(identity)
        _require(body==modified and updated['offset']==offset,'FUNCTION_ROUNDTRIP_FAILED')
        offset+=len(modified)
    _require(hashlib.sha256(result).hexdigest()==CANDIDATE_SHA256,'CANDIDATE_FINGERPRINT_MISMATCH')
    return result


def install_candidate(guard):
    """通过持久恢复守卫安装有限候选；默认Launcher不调用，外层必须管理测试时限和撤销。"""
    target=guard.root/RELATIVE
    _require(isinstance(CANDIDATE_SHA256,str) and len(CANDIDATE_SHA256)==64,'CANDIDATE_NOT_FROZEN')
    _require(20*1024*1024<target.stat().st_size<32*1024*1024,'SIZE_INVALID')
    variant=fix_standalone_owned_loadout(target.read_bytes())
    record=guard.apply(RELATIVE,variant)
    return dict(profile=PROFILE,source_sha256=SOURCE_SHA256,variant_sha256=record['variant_sha256'],
                native_live_verified=False,native_battle_effects_verified=False,online_branch_unchanged=True,
                requires_bounded_trial=True,asset_changes=False)
