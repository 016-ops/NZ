"""独立构造原生流送属性夹具，验证动态关卡不被遗漏以及未知数据不被当成空依赖。"""
from pathlib import Path
import struct
import tempfile
import unittest
import lzokay

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from tools.client_catalog import RESOURCE_KEY
from tools.native_map_streaming import inspect_streaming
from tests.test_native_package_dependencies import package_fixture


def streaming_fixture(*, bad_class=False, properties=None, compressed=False):
    """构造含一个真实Class导入和LevelStreaming导出的最小未压缩包，动态RoomA不在导入表。"""
    names = ['Core','Package','Engine','Class','LevelStreamingKismet','Stream','None',
             'PackageName','NameProperty','RoomA','bInitiallyLoaded','BoolProperty',
             'Bounds','StructProperty','Vector','VolumeUsage','ByteProperty','EUsage',
             'PackageNameToLoad','RoomB','StrProperty']

    def fname(name):
        """使用独立名称索引和零后缀，避免测试照抄生产解析偏移。"""
        return struct.pack('<II',names.index(name),0)

    def prop(name, kind, value, extra=b''):
        """独立编码UE3标签，Bool值属于标签而非长度声明。"""
        return fname(name)+fname(kind)+struct.pack('<II',len(value),0)+extra+value

    if properties is None:
        data = (bytes(4)+prop('PackageName','NameProperty',fname('RoomA'))
                +prop('bInitiallyLoaded','BoolProperty',b'',b'\x01')+fname('None'))
    else:
        data = properties(fname,prop)
    name_table = b''.join(struct.pack('<i',len(name)+1)+name.encode()+b'\0'+bytes(8) for name in names)
    imports = struct.pack('<iiiiiii',0,0,1,0,0,2,0)+struct.pack('<iiiiiii',0,0,3,0,-1,4,0)
    start = 125
    ioff,eo = start+len(name_table),start+len(name_table)+len(imports)
    export = bytearray(68)
    struct.pack_into('<iiiii',export,0,-3 if bad_class else -2,0,0,5,1)
    struct.pack_into('<iiii',export,32,len(data),eo+len(export),0,0)
    header = bytearray(144)
    struct.pack_into('<IIi',header,0,0x202fc,eo+len(export),5)
    header[12:17] = b'None\0'
    struct.pack_into('<IIIIII',header,21,len(names),start,1,eo,2,ioff)
    struct.pack_into('<I',header,81,1)
    payload=name_table+imports+export+data
    physical_start=start
    if compressed:
        # 物理压缩头增长16字节，但名称/导入/导出及属性始终沿用原逻辑偏移。
        packed=lzokay.compress(payload)
        block=struct.pack('<IIIIII',0x9e2a83c1,131072,len(packed),len(payload),len(packed),len(payload))+packed
        physical_start+=16
        struct.pack_into('<II',header,105,2,1)
        struct.pack_into('<IIII',header,113,start,len(payload),physical_start,len(block))
        payload=block
    cipher_size = (physical_start-8)//16*16
    encoder = Cipher(algorithms.AES(RESOURCE_KEY),modes.ECB()).encryptor()
    encrypted = encoder.update(bytes(header[:cipher_size]))+encoder.finalize()
    return struct.pack('<II',0x3e27a8ff,physical_start)+encrypted+bytes(physical_start-8-cipher_size)+payload


class MapStreamingTests(unittest.TestCase):
    """所有测试只读临时地图，解析通过也始终不能宣称资源闭包或实机通过。"""

    def inspect(self, data):
        """把独立夹具写入临时目录，按文件边界运行真实解析器并及时回收。"""
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'Map.udk'
            path.write_bytes(data)
            return inspect_streaming(path)

    def test_dynamic_package_is_separate_from_import_roots(self):
        """RoomA只在LevelStreaming属性中出现，必须补入动态依赖而非依赖导入表碰巧包含。"""
        result=self.inspect(streaming_fixture())
        self.assertEqual(result['root_packages'],['Engine'])
        self.assertEqual(result['streaming_packages'],['RoomA'])
        self.assertEqual(result['objects'][0]['name'],'Stream_0')
        self.assertTrue(result['objects'][0]['properties']['bInitiallyLoaded'])
        self.assertFalse(result['dependency_closure_verified'] or result['native_loaded'])

    def test_struct_byte_and_bool_tag_lengths_do_not_shift_package_name(self):
        """不同类型附加标签正确消耗，假值不能变为丢失属性，两个原生包字段均保留。"""
        def values(fname,prop):
            """独立组合三种标签头，再附加真正的两个关卡引用。"""
            return (bytes(4)+prop('Bounds','StructProperty',bytes(12),fname('Vector'))
                    +prop('VolumeUsage','ByteProperty',b'\x02',fname('EUsage'))
                    +prop('bInitiallyLoaded','BoolProperty',b'',b'\0')
                    +prop('PackageName','NameProperty',fname('RoomA'))
                    +prop('PackageNameToLoad','NameProperty',fname('RoomB'))+fname('None'))
        result=self.inspect(streaming_fixture(properties=values))
        self.assertEqual(result['streaming_packages'],['RoomA','RoomB'])
        self.assertIs(result['objects'][0]['properties']['bInitiallyLoaded'],False)

    def test_wrong_property_type_duplicate_and_missing_terminator_rejected(self):
        """损坏依赖属性、重复字段与尾部残余必须失败，不能产生空关卡清单。"""
        for scenario in ('wrong_type','duplicate','no_terminator','trailing','invalid_bool'):
            def values(fname,prop):
                """每次只损坏一个合同字段，保留其他包表合法以覆盖属性解析路径。"""
                content=bytes(4)+prop('PackageName','StrProperty' if scenario=='wrong_type' else 'NameProperty',fname('RoomA'))
                if scenario=='duplicate':content+=prop('PackageName','NameProperty',fname('RoomB'))
                if scenario=='invalid_bool':content+=prop('bInitiallyLoaded','BoolProperty',b'',b'\x02')
                if scenario!='no_terminator':content+=fname('None')
                if scenario=='trailing':content+=b'\0'
                return content
            with self.subTest(scenario=scenario),self.assertRaises(ValueError):
                self.inspect(streaming_fixture(properties=values))

    def test_invalid_class_reference_is_not_a_non_streaming_object(self):
        """越界类索引直接拒绝，不能跳过该导出后宣称没有动态依赖。"""
        with self.assertRaisesRegex(ValueError,'CLASS_REFERENCE_INVALID'):
            self.inspect(streaming_fixture(bad_class=True))

    def test_compressed_exports_and_empty_packages_keep_actual_scope(self):
        """LZO原生属性与未压缩一致；合法零导出包保持空列表，也不能宣称完整闭包。"""
        plain=self.inspect(streaming_fixture())
        compressed=self.inspect(streaming_fixture(compressed=True))
        self.assertEqual(compressed['compression'],2)
        self.assertEqual(compressed['objects'],plain['objects'])
        self.assertEqual(compressed['streaming_packages'],['RoomA'])
        empty=self.inspect(package_fixture(compressed=True))
        self.assertEqual(empty['objects'],[])
        self.assertFalse(empty['dependency_closure_verified'])

    def test_corrupt_compressed_data_is_not_empty_dependency_list(self):
        """LZO正文截断或损坏必须失败，不能当作没有动态关卡继续验收。"""
        data=streaming_fixture(compressed=True)
        for corrupt in (data[:-1],data[:-4]+bytes(4)):
            with self.subTest(size=len(corrupt)),self.assertRaises(ValueError):
                self.inspect(corrupt)
