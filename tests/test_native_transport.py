"""当前认证、研究换钥及业务取证的独立工具测试；仅临时 socket 和夹具，不运行游戏或发送邮件。"""
import json
from contextlib import contextmanager
import socket
import struct
import threading
import unittest
from unittest.mock import patch

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from adapters.cn_current.transport import (
    MAX_FRAME_BYTES, TransportError, decode_application, make_challenge, make_key_change, receive_application_frame,
    make_application_response, receive_frame, validate_auth, verify_challenge_reply,
)


@contextmanager
def socket_pair():
    """创建并最终关闭两个真实 socket，故障测试也不遗留测试连接。"""
    sender, receiver = socket.socketpair()
    try:
        yield sender, receiver
    finally:
        sender.close()
        receiver.close()


def frame(command, payload=b'', *, head_length=None, body_length=0, encrypted_header_length=0):
    """用独立测试夹具编码网络字节序头，不复用被测模块的打包函数。"""
    return struct.pack('>BBBBII', 0x55, 0x0E, command, encrypted_header_length,
                       12 + len(payload) if head_length is None else head_length, body_length) + payload


def auth(game_id=10002, token=b'local_test_token', *, encryption_method=3, service_id=0, auth_type=3):
    """构造已证 AUTH 字段，允许测试单独替换身份或标量以验证严格拒绝。"""
    return frame(3, struct.pack('>IIII', encryption_method, service_id, auth_type, game_id) + bytes([len(token)]) + token)


def encrypted_reply(key, plaintext):
    """直接使用密码库构造 SYNACK 夹具，避免以 make_challenge 的输出冒充对端实现。"""
    encoder = Cipher(algorithms.AES(key), modes.CBC(bytes(range(16)))).encryptor()
    encrypted = encoder.update(plaintext) + encoder.finalize()
    return frame(9, bytes([len(encrypted)]) + encrypted)


def asynchronous_reply(key, challenge_plaintext, sequence_plaintext):
    """独立编码当前异步 SYNACK：头扩展仍是挑战密文，正文另加一块序号 AES3 密文。"""
    compact = encrypted_reply(key, challenge_plaintext)
    encoder = Cipher(algorithms.AES(key), modes.CBC(bytes(range(16)))).encryptor()
    encrypted_body = encoder.update(sequence_plaintext) + encoder.finalize()
    return frame(9, compact[12:], body_length=len(encrypted_body), encrypted_header_length=4) + encrypted_body


def application_cipher_frame(key, padded_plaintext):
    """将独立测试明文直接 AES 加密为 cmd0；允许构造错误填充验证解码器拒绝路径。"""
    encoder = Cipher(algorithms.AES(key), modes.CBC(bytes(range(16)))).encryptor()
    encrypted_body = encoder.update(padded_plaintext) + encoder.finalize()
    return frame(0, body_length=len(encrypted_body), encrypted_header_length=4) + encrypted_body


def application_fixture(key, body, sequence=0x12345678):
    """按当前独立合同生成变长业务夹具，序号仅用于确认剥离边界，不作为业务字段解释。"""
    plaintext = struct.pack('>I', sequence) + body
    padding_length = (-(len(plaintext) + 6)) % 16 + 6
    return application_cipher_frame(key, plaintext + b'P' * (padding_length - 6) + b'tsf4g' + bytes([padding_length]))


class NativeTransportTests(unittest.TestCase):
    """隔离验证 TCP 分片、长度与命令门禁、预期账号绑定和 challenge 的加密边界。"""

    def setUp(self):
        """固定公开测试身份与密码学夹具，绝不读取业务凭据或真实客户端。"""
        self.game_id, self.token = 10002, 'local_test_token'
        self.key, self.challenge = bytes(range(16)), bytes(range(16, 32))

    def test_fragmented_socket_frames_do_not_overread_following_frame(self):
        """真实 socket 按不同分片发送连续帧，接收一次仅消费一帧且保留下一帧边界。"""
        first, second = auth(), frame(9, b'\x20' + bytes(32))
        with socket_pair() as (sender, receiver):
            receiver.settimeout(2)

            def write_fragments():
                """从单字节开始交错发送分片，最终半关闭写端以允许接收方观察明确 EOF。"""
                message = first + second
                sizes, offset = (1, 2, 5, 3, 13), 0
                while offset < len(message):
                    size = sizes[offset % len(sizes)]
                    sender.sendall(message[offset:offset + size])
                    offset += size
                sender.shutdown(socket.SHUT_WR)

            writer = threading.Thread(target=write_fragments)
            writer.start()
            self.assertEqual(receive_frame(receiver), first)
            self.assertEqual(receive_frame(receiver), second)
            with self.assertRaises(TransportError):
                receive_frame(receiver)
            writer.join(timeout=2)
            self.assertFalse(writer.is_alive())

    def test_fragment_truncation_and_socket_timeout_are_not_accepted(self):
        """截断头或负载明确失败；无数据等待保留调用方的 socket 超时而不无限阻塞。"""
        for content in (b'\x55\x0e', auth()[:-1]):
            with self.subTest(length=len(content)), socket_pair() as (sender, receiver):
                receiver.settimeout(1)
                sender.sendall(content)
                sender.shutdown(socket.SHUT_WR)
                with self.assertRaisesRegex(TransportError, 'TRUNCATED_FRAME'):
                    receive_frame(receiver)
        with socket_pair() as (sender, receiver):
            receiver.settimeout(0.02)
            with self.assertRaises(socket.timeout):
                receive_frame(receiver)

    def test_invalid_lengths_and_unknown_header_are_rejected_before_payload_read(self):
        """超限、头短、非零正文、未知命令与头标志仅凭 12 字节头立即拒绝，不等待声明的正文。"""
        headers = [frame(3, head_length=11), frame(3, head_length=MAX_FRAME_BYTES + 1),
                   frame(3, body_length=1), frame(99), frame(3, head_length=0xFFFFFFFF),
                   b'\x54' + frame(3)[1:], frame(3)[:1] + b'\x0f' + frame(3)[2:],
                   frame(3)[:3] + b'\x01' + frame(3)[4:]]
        for header in headers:
            with self.subTest(header=header.hex()), socket_pair() as (sender, receiver):
                receiver.settimeout(0.1)
                sender.sendall(header)
                with self.assertRaises(TransportError):
                    receive_frame(receiver)
        for limit in (True, 11, MAX_FRAME_BYTES + 1):
            with self.subTest(limit=limit), self.assertRaises(TransportError):
                receive_frame(None, max_bytes=limit)

    def test_auth_accepts_only_expected_identity_and_returns_no_token(self):
        """正确首包只返回非秘密结构摘要，令牌或其他账号即使结构有效也不能通过。"""
        summary = validate_auth(auth(), self.game_id, self.token)
        self.assertTrue(summary['expected_identity_verified'])
        self.assertEqual(summary['game_id'], self.game_id)
        self.assertNotIn(self.token, json.dumps(summary))
        # 当前捕获首包为 60 字节：12 字节头、16 字节标量、1 字节长度及 31 字节独立令牌。
        captured_shape = auth(token=b'R' * 31)
        self.assertEqual(captured_shape[:12], bytes.fromhex('550e03000000003c00000000'))
        self.assertEqual(validate_auth(captured_shape, self.game_id, 'R' * 31)['head_length'], 60)
        for content in (auth(game_id=10003), auth(token=b'wrong_token'), auth(token=b'local_test_tokeN')):
            with self.assertRaisesRegex(TransportError, 'AUTH_IDENTITY_MISMATCH') as error:
                validate_auth(content, self.game_id, self.token)
            self.assertNotIn(self.token, str(error.exception))

    def test_auth_rejects_unknown_scalars_commands_and_nonexact_lengths(self):
        """标量必须符合当前首包，拒绝尾随帧、令牌长度欺骗和错误预期身份配置。"""
        examples = [auth(encryption_method=0), auth(service_id=1), auth(auth_type=0),
                    auth() + b'x', auth()[:-1], frame(3, b'\x00' * 16), frame(8, b'\x00' * 17),
                    auth(token=b''), auth(token=b'x' * 129)]
        changed = bytearray(auth())
        changed[28] += 1
        examples.append(bytes(changed))
        for content in examples:
            with self.subTest(length=len(content)), self.assertRaises(TransportError):
                validate_auth(content, self.game_id, self.token)
        for expected_id, expected_token in ((True, self.token), (0, self.token), (2**31, self.token),
                                            (self.game_id, ''), (self.game_id, 'x\0y'), (self.game_id, 'x' * 129)):
            with self.assertRaises(TransportError):
                validate_auth(auth(), expected_id, expected_token)

    def test_challenge_has_proven_compact_frame_and_random_padding(self):
        """独立解密输出检查当前 IV、16 字节挑战、10 字节随机填充及 tsf4g/填充长度尾标。"""
        with patch('adapters.cn_current.transport.secrets.token_bytes', return_value=b'P' * 10) as random_bytes:
            encoded = make_challenge(self.key, self.challenge)
        random_bytes.assert_called_once_with(10)
        self.assertEqual(encoded[:13], bytes.fromhex('550e08000000002d0000000020'))
        decoder = Cipher(algorithms.AES(self.key), modes.CBC(bytes(range(16)))).decryptor()
        plaintext = decoder.update(encoded[13:]) + decoder.finalize()
        self.assertEqual(plaintext, self.challenge + b'P' * 10 + b'tsf4g\x10')
        self.assertNotEqual(make_challenge(self.key, self.challenge), make_challenge(self.key, self.challenge))

    def test_challenge_reply_checks_key_footer_and_constant_time_challenge(self):
        """对端合法 SYNACK 通过；错 key、错挑战、错尾标、错末尾长度和密文破坏均失败。"""
        plaintext = self.challenge + b'Q' * 10 + b'tsf4g\x10'
        reply = encrypted_reply(self.key, plaintext)
        self.assertTrue(verify_challenge_reply(reply, self.key, self.challenge))
        self.assertFalse(verify_challenge_reply(reply, b'X' * 16, self.challenge))
        self.assertFalse(verify_challenge_reply(reply, self.key, b'Y' * 16))
        for bad_plaintext in (plaintext[:-6] + b'wrong\x10', plaintext[:-1] + b'\x0f'):
            self.assertFalse(verify_challenge_reply(encrypted_reply(self.key, bad_plaintext), self.key, self.challenge))
        corrupt = reply[:-1] + bytes([reply[-1] ^ 1])
        self.assertFalse(verify_challenge_reply(corrupt, self.key, self.challenge))

    def test_challenge_reply_rejects_framing_and_crypto_input_errors(self):
        """格式错误不得进入解密；key/challenge 长度必须精确，拒绝隐式截断或未知命令。"""
        for content in (frame(9, b'\x10' + bytes(32)), frame(9, b'\x20' + bytes(31)),
                        frame(9, b'\x30' + bytes(48)), make_challenge(self.key, self.challenge),
                        frame(99, b'\x20' + bytes(32))):
            with self.assertRaises(TransportError):
                verify_challenge_reply(content, self.key, self.challenge)
        for key, challenge in ((b'x' * 15, self.challenge), (b'x' * 32, self.challenge),
                               (self.key, b'x' * 15), (self.key, b'x' * 17), ('x' * 16, self.challenge)):
            with self.assertRaises(TransportError):
                make_challenge(key, challenge)
            with self.assertRaises(TransportError):
                verify_challenge_reply(frame(9), key, challenge)

    def test_asynchronous_synack_checks_challenge_and_padded_sequence_body(self):
        """61 字节异步 SYNACK 同时验证挑战和正文填充，随机起始序号不强制为 0 或 1。"""
        challenge_plaintext = self.challenge + b'R' * 10 + b'tsf4g\x10'
        for sequence in (0, 0x12345678, 0xFFFFFFFF):
            sequence_plaintext = struct.pack('>I', sequence) + b'S' * 6 + b'tsf4g\x0c'
            reply = asynchronous_reply(self.key, challenge_plaintext, sequence_plaintext)
            self.assertEqual(len(reply), 61)
            self.assertEqual(reply[:12], bytes.fromhex('550e09040000002d00000010'))
            self.assertTrue(verify_challenge_reply(reply, self.key, self.challenge))
        for invalid_plaintext in (b'\0' * 4 + b'S' * 6 + b'wrong\x0c',
                                  b'\0' * 4 + b'S' * 6 + b'tsf4g\x0b'):
            reply = asynchronous_reply(self.key, challenge_plaintext, invalid_plaintext)
            self.assertFalse(verify_challenge_reply(reply, self.key, self.challenge))

    def test_asynchronous_synack_receive_counts_body_and_preserves_next_frame(self):
        """真实连接按字节发送含正文的 SYNACK，接收包括 16 字节正文且不吞掉下一 AUTH。"""
        reply = asynchronous_reply(self.key, self.challenge + b'R' * 10 + b'tsf4g\x10',
                                   b'\x12\x34\x56\x78' + b'S' * 6 + b'tsf4g\x0c')
        with socket_pair() as (sender, receiver):
            receiver.settimeout(1)
            for value in reply + auth():
                sender.sendall(bytes([value]))
            self.assertEqual(receive_frame(receiver, max_bytes=61), reply)
            self.assertEqual(receive_frame(receiver), auth())
        with socket_pair() as (sender, receiver):
            sender.sendall(reply[:12])
            receiver.settimeout(0.1)
            with self.assertRaisesRegex(TransportError, 'INVALID_FRAME_LENGTH'):
                receive_frame(receiver, max_bytes=60)

    def test_only_proven_synack_encrypted_body_shape_is_accepted(self):
        """仅 cmd9/EncHeadLen4/HeadLen45/BodyLen16 开放；其余命令或尺寸不借机放宽。"""
        layouts = ((3, 4, 45, 16), (8, 4, 45, 16), (9, 0, 45, 16), (9, 4, 45, 0),
                   (9, 4, 44, 16), (9, 4, 45, 32), (9, 8, 45, 16))
        for command, encrypted_length, head_length, body_length in layouts:
            header = frame(command, head_length=head_length, body_length=body_length,
                           encrypted_header_length=encrypted_length)
            with self.subTest(layout=(command, encrypted_length, head_length, body_length)), socket_pair() as (sender, receiver):
                receiver.settimeout(0.1)
                sender.sendall(header)
                with self.assertRaisesRegex(TransportError, 'UNSUPPORTED_ENCRYPTED_BODY'):
                    receive_frame(receiver)


class NativeApplicationTransportTests(unittest.TestCase):
    """独立验证研究换钥与只读业务解密接口，不通过认证接收器开放业务或执行未知消息。"""

    def setUp(self):
        """公开固定测试 key 仅用于工具夹具，与任何客户端和业务会话无关。"""
        self.old_key, self.new_key = bytes(range(16)), bytes(range(32, 48))

    def test_key_change_encodes_exact_current_parser_contract(self):
        """48 字节 CHGSKEY 使用旧 key 加密 16 字节新 key，Type0 只作为显式研究选择。"""
        with patch('adapters.cn_current.transport.secrets.token_bytes', return_value=b'P' * 10):
            encoded = make_key_change(self.old_key, self.new_key)
        self.assertEqual(len(encoded), 48)
        self.assertEqual(encoded[:16], bytes.fromhex('550e0100000000300000000000000020'))
        decoder = Cipher(algorithms.AES(self.old_key), modes.CBC(bytes(range(16)))).decryptor()
        plaintext = decoder.update(encoded[16:]) + decoder.finalize()
        self.assertEqual(plaintext, self.new_key + b'P' * 10 + b'tsf4g\x10')

    def test_key_change_requires_exact_byte_keys(self):
        """旧 key 和新 key 都必须精确 16 字节，不截断字符串或采用未知长度算法。"""
        for old_key, new_key in ((b'x' * 15, self.new_key), (b'x' * 32, self.new_key),
                                 (self.old_key, b'y' * 15), (self.old_key, b'y' * 17),
                                 ('x' * 16, self.new_key), (self.old_key, 'y' * 16)):
            with self.assertRaises(TransportError):
                make_key_change(old_key, new_key)

    def test_application_receiver_handles_fragmentation_without_crossing_frames(self):
        """真实 socket 分片接收两帧 cmd0，并在截断密文时报告 EOF 而非返回半帧。"""
        first = application_cipher_frame(self.new_key, b'\0' * 4 + b'R' * 6 + b'tsf4g\x0c')
        second = application_cipher_frame(self.new_key, b'\xff' * 4 + b'S' * 6 + b'tsf4g\x0c')
        with socket_pair() as (sender, receiver):
            receiver.settimeout(1)
            for offset in range(0, len(first + second), 3):
                sender.sendall((first + second)[offset:offset + 3])
            self.assertEqual(receive_application_frame(receiver), first)
            self.assertEqual(receive_application_frame(receiver), second)
        with socket_pair() as (sender, receiver):
            receiver.settimeout(1)
            sender.sendall(first[:-1])
            sender.shutdown(socket.SHUT_WR)
            with self.assertRaisesRegex(TransportError, 'TRUNCATED_FRAME'):
                receive_application_frame(receiver)

    def test_application_receiver_rejects_unknown_shapes_and_total_length(self):
        """业务接口限定 cmd0/EncHeadLen4/HeadLen12 和完整 AES 块，长度门禁覆盖头加正文。"""
        headers = [frame(command, body_length=16, encrypted_header_length=4) for command in (1, 3, 8, 9, 99)]
        headers.extend((frame(0, body_length=16), frame(0, head_length=13, body_length=16, encrypted_header_length=4),
                        frame(0, body_length=0, encrypted_header_length=4),
                        frame(0, body_length=15, encrypted_header_length=4),
                        frame(0, body_length=17, encrypted_header_length=4),
                        frame(0, body_length=65536, encrypted_header_length=4)))
        for header in headers:
            with self.subTest(header=header.hex()), socket_pair() as (sender, receiver):
                receiver.settimeout(0.1)
                sender.sendall(header)
                with self.assertRaises(TransportError):
                    receive_application_frame(receiver)
        with socket_pair() as (sender, receiver):
            receiver.settimeout(0.1)
            sender.sendall(frame(0, body_length=16, encrypted_header_length=4))
            with self.assertRaisesRegex(TransportError, 'INVALID_APPLICATION_LENGTH'):
                receive_application_frame(receiver, max_bytes=27)
        for limit in (True, 11, MAX_FRAME_BYTES + 1):
            with self.assertRaises(TransportError):
                receive_application_frame(None, max_bytes=limit)

    def test_authentication_receiver_stays_closed_to_application_and_key_change(self):
        """增加新研究接口不能放宽旧认证接收器；cmd0 与 CHGSKEY 入站仍明确拒绝。"""
        for content in (frame(0, body_length=16, encrypted_header_length=4),
                        make_key_change(self.old_key, self.new_key)):
            with socket_pair() as (sender, receiver):
                receiver.settimeout(0.1)
                sender.sendall(content)
                with self.assertRaisesRegex(TransportError, 'UNSUPPORTED_COMMAND'):
                    receive_frame(receiver)

    def test_application_decode_strips_sequence_and_variable_padding_boundaries(self):
        """覆盖空业务、padding6/21 两侧及最大允许正文，返回值只含原业务字节。"""
        for size in (0, 1, 5, 6, 7, 11, 12, 13, 128, 65510):
            body = (b'\x00\xff' * ((size + 1) // 2))[:size]
            # 直接使用指定大小夹具，覆盖 65532 字节最大完整块帧（低于总上限 65536）。
            content = application_fixture(self.new_key, body)
            with self.subTest(size=size):
                self.assertLessEqual(len(content), MAX_FRAME_BYTES)
                self.assertEqual(decode_application(content, self.new_key), body)
                self.assertEqual(decode_application(application_fixture(self.new_key, body, 0xFFFFFFFF), self.new_key), body)
        self.assertEqual(len(application_fixture(self.new_key, b'X' * 65510)), 65532)

    def test_application_decode_rejects_wrong_key_footer_and_padding(self):
        """错 key、尾标及危险 padding 值均只给固定错误码，不回显任何解密业务内容。"""
        content = application_fixture(self.new_key, b'private-test-body')
        with self.assertRaisesRegex(TransportError, 'INVALID_APPLICATION_PADDING'):
            decode_application(content, b'K' * 16)
        for padding in (0, 5, 13, 22, 255):
            plaintext = b'\0' * 4 + b'P' * 6 + b'tsf4g' + bytes([padding])
            with self.subTest(padding=padding), self.assertRaisesRegex(TransportError, 'INVALID_APPLICATION_PADDING'):
                decode_application(application_cipher_frame(self.new_key, plaintext), self.new_key)
        wrong_footer = b'\0' * 4 + b'P' * 6 + b'wrong\x0c'
        with self.assertRaisesRegex(TransportError, 'INVALID_APPLICATION_PADDING'):
            decode_application(application_cipher_frame(self.new_key, wrong_footer), self.new_key)
        corrupted = content[:-1] + bytes([content[-1] ^ 1])
        with self.assertRaises(TransportError):
            decode_application(corrupted, self.new_key)

    def test_application_decode_requires_exact_frame_and_key(self):
        """解码入口也独立核验完整边界与 key，不依赖调用方一定经过 receive。"""
        content = application_fixture(self.new_key, b'private-test-body')
        oversized = application_fixture(self.new_key, b'X' * 65511)
        for invalid in (content[:-1], content + b'x', content[:11], auth(), oversized,
                        frame(0, body_length=16, encrypted_header_length=4),
                        b'\x54' + content[1:], content[:1] + b'\x0f' + content[2:]):
            with self.subTest(length=len(invalid)), self.assertRaises(TransportError):
                decode_application(invalid, self.new_key)
        for key in (b'K' * 15, b'K' * 32, 'K' * 16):
            with self.assertRaisesRegex(TransportError, 'INVALID_NEW_SESSION_KEY'):
                decode_application(content, key)


class NativeDownlinkTransportTests(unittest.TestCase):
    """独立解密当前研究下行帧，验证完整业务不加序号及所有 AES3 填充/长度边界。"""

    def setUp(self):
        """仅使用公开测试 key；业务夹具为不带消息语义的任意二进制 bytes。"""
        self.key = bytes(range(32, 48))

    def decrypt_independently(self, encoded):
        """独立读取头并用密码库解密，不能复用上行 decoder 因为其会剥离四字节序号。"""
        magic, version, command, encrypted_header, head_length, body_length = struct.unpack('>BBBBII', encoded[:12])
        self.assertEqual((magic, version, command, encrypted_header, head_length), (0x55, 0x0E, 0, 0, 12))
        self.assertEqual(body_length, len(encoded) - 12)
        self.assertEqual(body_length % 16, 0)
        decoder = Cipher(algorithms.AES(self.key), modes.CBC(bytes(range(16)))).decryptor()
        return decoder.update(encoded[12:]) + decoder.finalize()

    def test_downlink_keeps_complete_application_without_sequence_prefix(self):
        """明文开头必须就是调用方业务首字节；任何上行风格序号前缀都会破坏该断言。"""
        application = b'\x82\x02\x10\x20\x00\xffindependent-body'
        encoded = make_application_response(application, self.key)
        plaintext = self.decrypt_independently(encoded)
        padding = plaintext[-1]
        self.assertEqual(plaintext[:-padding], application)
        self.assertEqual(plaintext[:4], application[:4])
        self.assertEqual(plaintext[-6:-1], b'tsf4g')
        # 下行必须继续与上行接口隔离，不能把 EncHeadLen0 错读成需要剥序号的上行帧。
        with self.assertRaisesRegex(TransportError, 'UNSUPPORTED_APPLICATION_HEADER'):
            decode_application(encoded, self.key)

    def test_downlink_covers_all_custom_padding_lengths_and_random_byte_counts(self):
        """逐个明文余数覆盖 padding6..21，随机填充数量与最终长度/尾标均由独立解密核对。"""
        seen = set()
        for size in range(1, 33):
            application = bytes(range(size))
            expected_padding = (-(size + 6)) % 16 + 6
            with patch('adapters.cn_current.transport.secrets.token_bytes', return_value=b'R' * (expected_padding - 6)) as random_bytes:
                encoded = make_application_response(application, self.key)
            random_bytes.assert_called_once_with(expected_padding - 6)
            plaintext = self.decrypt_independently(encoded)
            self.assertEqual(plaintext, application + b'R' * (expected_padding - 6) + b'tsf4g' + bytes([expected_padding]))
            seen.add(plaintext[-1])
        self.assertEqual(seen, set(range(6, 22)))

    def test_downlink_maximum_frame_boundary_is_checked_before_encrypting(self):
        """最大合法正文为65514字节，填充后总帧65532；再多一字节需下一AES块并超限拒绝。"""
        application = b'X' * 65514
        encoded = make_application_response(application, self.key)
        self.assertEqual(len(encoded), 65532)
        self.assertEqual(self.decrypt_independently(encoded)[:-6], application)
        with patch('adapters.cn_current.transport.secrets.token_bytes') as random_bytes:
            for size in (65515, 65536, 131072):
                with self.subTest(size=size), self.assertRaisesRegex(TransportError, 'INVALID_APPLICATION_LENGTH'):
                    make_application_response(b'X' * size, self.key)
            random_bytes.assert_not_called()

    def test_downlink_rejects_empty_or_nonbytes_application_and_invalid_keys(self):
        """空业务、可变缓冲及字符串不隐式转换；key类型或长度不符时明确拒绝。"""
        for application in (b'', bytearray(b'x'), memoryview(b'x'), 'x', None):
            with self.assertRaisesRegex(TransportError, 'INVALID_APPLICATION_PAYLOAD'):
                make_application_response(application, self.key)
        for key in (b'', b'K' * 15, b'K' * 17, b'K' * 32, bytearray(self.key), 'K' * 16):
            with self.assertRaisesRegex(TransportError, 'INVALID_DOWNLINK_KEY'):
                make_application_response(b'private-test-body', key)
