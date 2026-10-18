"""当前功能消息的有界TDR结构编解码；每个启用消息仍须当前DLL及实收协议验收。"""
from functools import lru_cache
import hashlib
import json
from pathlib import Path
import struct
from .zone import parse_zone_header, ZoneProtocolError

SCHEMA_SHA256 = '678e614093a4b179efd3824b10748d8e6a09aebd3d030c5b9ca901e5f9e99b25'
# TDR type19为IPv4的32位原值；只保留整数，不从客户端提供值选择服务器连接目标。
PRIMITIVES = {2:'b',3:'B',5:'h',6:'H',7:'i',8:'I',11:'q',12:'Q',15:'q',19:'I'}


@lru_cache(maxsize=1)
def metadata():
    """固定元数据派生物摘要，未知版本拒绝而不是沿用旧偏移。"""
    raw=(Path(__file__).resolve().parents[2]/'config/cn2020-feature-protocol-v13.json').read_bytes()
    if hashlib.sha256(raw).hexdigest()!=SCHEMA_SHA256:
        raise ZoneProtocolError('FEATURE_SCHEMA_MISMATCH')
    return json.loads(raw)


def normalized(schema, values):
    """补齐零值并由数组推导计数；显式计数冲突、未知字段或不完整数组拒绝。"""
    if not isinstance(values,dict):
        raise ZoneProtocolError('FEATURE_FIELDS_REQUIRED')
    fields=metadata()['schemas'][schema]['fields']
    if set(values)-{f['name'] for f in fields}:
        raise ZoneProtocolError('FEATURE_UNKNOWN_FIELD')
    result=dict(values)
    for field in fields:
        if field['capacity']>1:
            value=result.get(field['name'],[] if field['reference'] else [{} if field['type']==1 else 0 for _ in range(field['capacity'])])
            if not isinstance(value,list) or len(value)>field['capacity']:
                raise ZoneProtocolError('FEATURE_ARRAY_LIMIT')
            if field['reference']:
                if field['reference'] in result and result[field['reference']]!=len(value):
                    raise ZoneProtocolError('FEATURE_ARRAY_COUNT')
                result[field['reference']]=len(value)
            elif len(value)!=field['capacity']:
                raise ZoneProtocolError('FEATURE_FIXED_ARRAY')
            result[field['name']]=value
    return result


def encode(schema, values, *, host=False, depth=0):
    """编码动态数组、严格CP936字符串和整数；host模式只用于离线DLL核验。"""
    if depth>16:
        raise ZoneProtocolError('FEATURE_DEPTH')
    meta=metadata()['schemas'][schema]
    values=normalized(schema,values)
    output=bytearray(meta['host_size']) if host else bytearray()
    for field in meta['fields']:
        array=field['capacity']>1
        entries=values.get(field['name'],[]) if array else [values.get(field['name'],{} if field['type']==1 else '' if field['type']==21 else 0)]
        for index,value in enumerate(entries):
            if field['type']==1:
                piece=encode(field['nested'],value,host=host,depth=depth+1)
            elif field['type']==21:
                if not isinstance(value,str) or '\0' in value:
                    raise ZoneProtocolError('FEATURE_STRING')
                try:
                    encoded=value.encode('cp936')+b'\0'
                except UnicodeError:
                    raise ZoneProtocolError('FEATURE_STRING_ENCODING') from None
                if len(encoded)>field['string_capacity']:
                    raise ZoneProtocolError('FEATURE_STRING_LIMIT')
                piece=encoded.ljust(field['size'],b'\0') if host else struct.pack('>I',len(encoded))+encoded
            elif field['type'] in PRIMITIVES:
                if type(value) is not int:
                    raise ZoneProtocolError('FEATURE_INTEGER')
                try:
                    piece=struct.pack(('<' if host else '>')+PRIMITIVES[field['type']],value)
                except struct.error:
                    raise ZoneProtocolError('FEATURE_INTEGER_RANGE') from None
            else:
                raise ZoneProtocolError('FEATURE_UNVERIFIED_TYPE')
            if host:
                # 嵌套结构的字段size保留TDR旧版本声明，不能作为当前数组元素跨度。
                # 例如BasicMatchRoomInfo旧size68而当前host_size192；用68会覆盖下一房间。
                stride=metadata()['schemas'][field['nested']]['host_size'] if field['type']==1 else field['size']
                offset=field['offset']+index*stride
                if offset+len(piece)>len(output):
                    raise ZoneProtocolError('FEATURE_HOST_RANGE')
                output[offset:offset+len(piece)]=piece
            else:
                output.extend(piece)
                if len(output)>65535:
                    raise ZoneProtocolError('FEATURE_PACKET_LIMIT')
    return bytes(output)


def decode(schema, body, *, offset=0, depth=0):
    """按实际剩余正文解析字段；动态计数必须先出现，不允许超限或截断默认为零。"""
    if depth>16:
        raise ZoneProtocolError('FEATURE_DEPTH')
    result={}
    def take(size):
        """有界读取并移动当前位置，拒绝任何截断。"""
        nonlocal offset
        if size<0 or offset+size>len(body):
            raise ZoneProtocolError('FEATURE_TRUNCATED')
        value=body[offset:offset+size];offset+=size
        return value
    for field in metadata()['schemas'][schema]['fields']:
        array=field['capacity']>1
        count=result.get(field['reference']) if array and field['reference'] else field['capacity'] if array else 1
        if type(count) is not int or not 0<=count<=field['capacity']:
            raise ZoneProtocolError('FEATURE_ARRAY_COUNT')
        entries=[]
        for _ in range(count):
            if field['type']==1:
                value,offset=decode(field['nested'],body,offset=offset,depth=depth+1)
            elif field['type']==21:
                size=struct.unpack('>I',take(4))[0]
                if not 1<=size<=field['string_capacity']:
                    raise ZoneProtocolError('FEATURE_STRING_LIMIT')
                raw=take(size)
                if raw[-1:]!=b'\0' or b'\0' in raw[:-1]:
                    raise ZoneProtocolError('FEATURE_STRING_TERMINATION')
                try:
                    value=raw[:-1].decode('cp936')
                except UnicodeError:
                    raise ZoneProtocolError('FEATURE_STRING_ENCODING') from None
            elif field['type'] in PRIMITIVES:
                fmt='>'+PRIMITIVES[field['type']]
                value=struct.unpack(fmt,take(struct.calcsize(fmt)))[0]
            else:
                raise ZoneProtocolError('FEATURE_UNVERIFIED_TYPE')
            entries.append(value)
        result[field['name']]=entries if array else entries[0]
    return result,offset


def packet(command, values):
    """组装已登记命令的完整应用包，不执行网络发送。"""
    schema=metadata()['commands'].get(str(command))
    if not schema:
        raise ZoneProtocolError('FEATURE_UNKNOWN_COMMAND')
    body=encode(schema,values)
    return struct.pack('>HHHH',0x3243,command,8,len(body))+body


def parse(packet_bytes):
    """验证包头及全部正文消费，返回当前命令和字段；附加垃圾字节也拒绝。"""
    header=parse_zone_header(packet_bytes)
    schema=metadata()['commands'].get(str(header['command']))
    if not schema:
        raise ZoneProtocolError('FEATURE_UNKNOWN_COMMAND')
    values,end=decode(schema,packet_bytes,offset=8)
    if end!=len(packet_bytes):
        raise ZoneProtocolError('FEATURE_TRAILING_DATA')
    return header['command'],values


def host_fixture(command, values):
    """生成固定包容量的x86主机夹具，与Python线上包分别交给原生DLL验证。"""
    wire=packet(command,values)
    body=encode(metadata()['commands'][str(command)],values,host=True)
    host=struct.pack('<HHHH',0x3243,command,8,len(wire)-8)+body
    if len(host)>1025331:
        raise ZoneProtocolError('FEATURE_HOST_LIMIT')
    return host.ljust(1025331,b'\0'),wire
