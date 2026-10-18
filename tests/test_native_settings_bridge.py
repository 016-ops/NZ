"""设置桥的协议golden和服务提交确认；只用内存API替身，不读凭据、不发邮件或启动游戏。"""
import base64
import hashlib
import struct
from unittest import TestCase
from unittest.mock import Mock

from adapters.cn_current.zone import (ZONE_METADATA_SHA256, ZONE_OPAQUE_PLAYER_DEFINE_PROFILE,
    parse_set_player_define_request, build_player_define_data_response, build_set_player_define_committed_response,
    ZoneProtocolError)
from tools.native_settings_bridge import NativeSettingsBridge, decode_settings_response


def result(payload=b'', revision=0, **changes):
    """构造规范服务响应替身，便于分别注入摘要、长度、版本及提交状态错误。"""
    return dict(dict(metadata_sha256=ZONE_METADATA_SHA256, revision=revision, bytes=len(payload),
        payload_base64=base64.b64encode(payload).decode(), payload_sha256=hashlib.sha256(payload).hexdigest()), **changes)


class NativeSettingsCodecTests(TestCase):
    """固定当前DLL独立host结果，覆盖空值、104B实收长度和3200B上限及畸形长度。"""
    def test_current_native_tdr_goldens_for_requests_and_get_responses(self):
        """六组独立DLL全包摘要，防止把内部不透明正文当作别的版本短设置格式。"""
        fixtures = [(0, '51f1f47563816b66f51a82e6e88cee5de48339bc2899c4cb6ee89af4468f0bd8', '5568769e481a4c956bc5376f80cbb7c4614c8a311f3175f15275fb288e113080'),
                    (104, 'b58a3ef6a789582d62cd066b321ab972a04743cfaa0b4d9c130a40786817f310', 'ac8a4318b7f48a917bddbee1ecbf2152d07fdf1077ab88bb3146d3d37fa033df'),
                    (3200, '7e13b87fe968834ecb45e873350870f0f2a683e0d6452addfcfb5ce1b9945d53', 'a709e3d4699a64d29837739a88663540ae2ca5457689bee63860cf24f62439b7')]
        for count, request_sha, response_sha in fixtures:
            payload=bytes(i%256 for i in range(count))
            request=struct.pack('>HHHHI',0x3243,0xA202,8,4+count,count)+payload
            self.assertEqual(hashlib.sha256(request).hexdigest(),request_sha)
            self.assertEqual(parse_set_player_define_request(request),payload)
            response=build_player_define_data_response(payload,profile=ZONE_OPAQUE_PLAYER_DEFINE_PROFILE)
            self.assertEqual(hashlib.sha256(response).hexdigest(),response_sha)
        ack=build_set_player_define_committed_response(profile=ZONE_OPAQUE_PLAYER_DEFINE_PROFILE)
        self.assertEqual(hashlib.sha256(ack).hexdigest(),'b18ac912293a5330e930e9758d584536a819f3536be3303a6b7d89f44c0bed25')

    def test_mismatched_length_oversized_data_and_other_profiles_are_rejected(self):
        """不透明不等于无边界；长度、当前消息及profile均需精确一致。"""
        for declared,payload in ((1,b''),(0,b'x'),(3201,b'x'*3201)):
            request=struct.pack('>HHHHI',0x3243,0xA202,8,4+len(payload),declared)+payload
            with self.assertRaises(ZoneProtocolError):parse_set_player_define_request(request)
        with self.assertRaises(ZoneProtocolError):build_player_define_data_response(b'x'*3201,profile=ZONE_OPAQUE_PLAYER_DEFINE_PROFILE)
        with self.assertRaises(ZoneProtocolError):build_set_player_define_committed_response(profile='ph')


class NativeSettingsBridgeTests(TestCase):
    """服务超时、冲突或错误回执不能推进本地版本，成功才可交给原生应答构造器。"""
    def test_confirmed_save_advances_revision_and_replays_only_exact_server_bytes(self):
        """加载、保存均绑定游戏凭据/客户端/TDR；相同正文丢应答重试接受服务端幂等结果。"""
        client=Mock();client.request.side_effect=[result(),result(b'new',1,status='SAVED'),result(b'new',1,status='UNCHANGED')]
        bridge=NativeSettingsBridge(client,'fixture-game-token','a'*64)
        committed=bridge.save(b'new')
        self.assertEqual(committed['revision'],1)
        self.assertEqual(bridge.data,b'new')
        self.assertEqual(bridge.save(b'new'),committed)
        self.assertEqual(client.request.call_args.args[2],'fixture-game-token')
        self.assertEqual(client.request.call_args.args[1]['expected_revision'],1)
        self.assertEqual(client.request.call_args.args[1]['metadata_sha256'],ZONE_METADATA_SHA256)
        self.assertNotIn('fixture-game-token',str(committed))

    def test_timeout_wrong_data_revision_and_status_leave_loaded_snapshot_intact(self):
        """确认失败即抛出，旧快照不变；调用方不会得到可用来回复成功的提交摘要。"""
        for failure in (TimeoutError('fixture timeout'),result(b'other',2,status='SAVED'),
                        result(b'new',1,status='SAVED'),result(b'new',2,status='PENDING')):
            client=Mock();client.request.side_effect=[result(b'old',1),failure]
            bridge=NativeSettingsBridge(client,'fixture-game-token','a'*64)
            with self.subTest(failure_type=type(failure).__name__), self.assertRaises((TimeoutError,ValueError)):
                bridge.save(b'new')
            self.assertEqual((bridge.revision,bridge.data),(1,b'old'))

    def test_corrupt_server_payload_and_unverified_metadata_are_not_treated_as_empty(self):
        """摘要、长度、base64、TDR或零版本异常全部拒绝，不能回退为空设置造成覆盖。"""
        for value in (result(b'x',1,payload_sha256='0'*64),result(b'x',1,bytes=0),dict(result(b'x',1),revision=True),
                      result(b'x',1,metadata_sha256='b'*64),result(b'x',0),result(payload_base64='AB==')):
            with self.assertRaises(ValueError):decode_settings_response(value)
