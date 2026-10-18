"""原生房间协议边界与旧目录兼容；DLL独立验收另记，不以本模块替代客户端实机。"""
import json
from pathlib import Path
import struct
import unittest
from adapters.cn_current.features import metadata,packet,parse,encode
from adapters.cn_current.zone import ZoneProtocolError
from tools.native_room_protocol import COMMANDS


class NativeRoomProtocolTests(unittest.TestCase):
    """核对来源兼容、嵌套数组真实跨度和线上有界拒绝，防止多房/多席静默串数据。"""

    def test_previous_schemas_and_commands_are_identical(self):
        """v13增加菜单应答，原v12全部字段、命令及常量逐项不变。"""
        old=json.loads((Path(__file__).resolve().parents[1]/'config/cn2020-feature-protocol-v12.json').read_text(encoding='utf-8'))
        new=metadata()
        for key,value in old['schemas'].items():self.assertEqual(new['schemas'][key],value)
        for key,value in old['commands'].items():self.assertEqual(new['commands'][key],value)
        self.assertEqual(old['constants'],new['constants'])
        self.assertTrue(all(str(c) in new['commands'] for c in COMMANDS))
        self.assertNotIn(str(0xa11b),new['commands'])

    def test_maximum_room_array_uses_current_nested_host_size(self):
        """32个房间不能按旧68字节跨度重叠；后续房间ID和地图在各自192字节槽保存。"""
        rooms=[dict(Id=2**40+i,DisplayeId=i+1,Name='房间'+str(i),OwnerName='测试',
                    MatchSettings=dict(ModeId=8197,MapId=168,SubModeId=4098),FighterCapacity=8) for i in range(32)]
        value=dict(Result=0x8100,TotalRoomNum=32,RoomInfos=rooms)
        wire=packet(0xa102,value);_,decoded=parse(wire)
        self.assertEqual([r['Id'] for r in decoded['RoomInfos']],[r['Id'] for r in rooms])
        host=encode('ZN2C_ResMatchRoomList',value,host=True)
        self.assertEqual(len(host),6162)
        self.assertEqual(metadata()['schemas']['ZN2C_ResMatchRoomList']['fields'][3]['size'],68)
        for i in range(32):
            self.assertEqual(struct.unpack_from('<Q',host,6+i*192)[0],2**40+i)
        self.assertEqual(struct.unpack_from('<iii',host,6150),(0,0,0))

    def test_native_password_and_room_count_boundaries(self):
        """口令最多7个CP936字节；超32房、截断、附加字节和冲突计数都必须拒绝。"""
        packet(0xa10a,dict(Name='房间',Password='1234567'))
        for value in ('12345678','四个汉字'):
            with self.assertRaises(ZoneProtocolError):packet(0xa10a,dict(Password=value))
        for value in (dict(RoomInfos=[{}]*33),dict(RoomCount=2,RoomInfos=[{}])):
            with self.assertRaises(ZoneProtocolError):packet(0xa102,value)
        wire=packet(0xa103,dict(RoomId=2**64-1,Password=''))
        for malformed in (wire[:-1],wire+b'\0'):
            with self.assertRaises(ZoneProtocolError):parse(malformed)

    def test_ipv4_and_signed_port_layout_remains_bounded(self):
        """地址只做无符号32位编解码；端口是原TDR有符号16位，不允许溢出或任意新字段。"""
        value=dict(Ip=0x0100007f,Port=7777,DSKey=list(range(16)),Domain='',IsUseDomain=0)
        host=encode('GameServerInfo',value,host=True)
        self.assertEqual(struct.unpack_from('<Ih',host),(0x0100007f,7777))
        for wrong in (dict(value,Ip=-1),dict(value,Port=65535),dict(value,Unknown=1)):
            with self.assertRaises(ZoneProtocolError):encode('GameServerInfo',wrong)

    def test_room_password_packets_are_not_raw_diagnostic_evidence(self):
        """建房/加入/跟随口令只记录命令及长度，不留正文或可快速猜测的整包摘要。"""
        from tools.native_email_boot import application_observation
        for command,values in ((0xa10a,dict(Name='房间',Password='12345')),
                               (0xa103,dict(RoomId=1001,Password='12345')),
                               (0xa120,dict(RoomId=1001,Password='12345'))):
            result,capturable=application_observation(packet(command,values))
            self.assertFalse(capturable);self.assertNotIn('sha256',result);self.assertNotIn('12345',repr(result))
        result,capturable=application_observation(packet(0xa100,dict(RoomCount=32)))
        self.assertTrue(capturable);self.assertIn('sha256',result)


if __name__=='__main__':unittest.main()
