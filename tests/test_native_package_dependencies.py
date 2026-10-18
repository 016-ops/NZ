"""真实临时文件验证资源表边界、LZO块和依赖递归；不需要或复制真实客户端二进制。"""
from pathlib import Path
import struct
import tempfile
import unittest

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
import lzokay

from tools.client_catalog import RESOURCE_KEY
from tools.native_package_dependencies import import_table, inventory, decompress_chunk


def package_fixture(*, compressed=False, group=b'None\0', outer=0, script=False):
    """以独立结构构造764/2包，表数据和LZO来自实际编码库，未知首部尾部故意不赋予含义。"""
    names = b''.join(struct.pack('<i', len(name) + 1) + name + b'\0' + bytes(8)
                     for name in (b'Core', b'Package', b'Engine'))
    imports = struct.pack('<iiiiiii', 0, 0, 1, 0, outer, 2, 0)
    delta = len(group) - 5
    table_start = 125 + delta
    table = names + imports
    header = bytearray(141 + delta)
    struct.pack_into('<IIi', header, 0, 0x202fc, table_start + len(table), len(group))
    header[12:12 + len(group)] = group
    struct.pack_into('<IIIIII', header, 21 + delta, 3, table_start, 0,
                     table_start + len(table), 1, table_start + len(names))
    struct.pack_into('<I', header, 81 + delta, 1)
    struct.pack_into('<I', header, 105 + delta, 2 if compressed else 0)
    physical_start = table_start + (16 if compressed else 0)
    if compressed:
        encoded = lzokay.compress(table)
        block = struct.pack('<IIIIII', 0x9e2a83c1, 131072, len(encoded), len(table), len(encoded), len(table)) + encoded
        struct.pack_into('<I', header, 109 + delta, 1)
        struct.pack_into('<IIII', header, 113 + delta, table_start, len(table), physical_start, len(block))
    else:
        block = table
    count = (physical_start - 8) // 16 * 16
    key = RESOURCE_KEY[:5] + b'\x55' + RESOURCE_KEY[6:] if script else RESOURCE_KEY
    encoder = Cipher(algorithms.AES(key), modes.ECB()).encryptor()
    encrypted = encoder.update(bytes(header[:count])) + encoder.finalize()
    return struct.pack('<II', 0x3e27a900 if script else 0x3e27a8ff, physical_start) + encrypted + bytes(physical_start - 8 - count) + block


class PackageDependenciesTests(unittest.TestCase):
    """完整表偏移、独立块总长和保留未知依赖是核心合同；导入成功永不代表可加载。"""

    def inspect(self, data):
        """在实际临时文件上解析后立即回收，仅返回元数据。"""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'Seed.upk'
            path.write_bytes(data)
            return import_table(path)

    def test_plain_compressed_and_variable_group_resolve_identical_imports(self):
        """完整AES块、None及其他长度分组、末描述符不完整大小均由独立块头严格校验。"""
        for compressed in (False, True):
            for group in (b'None\0', b'FX\0', b'Weapons\0'):
                with self.subTest(compressed=compressed, group=group):
                    result = self.inspect(package_fixture(compressed=compressed, group=group))
                    self.assertEqual(result['root_packages'], ['Engine'])
                    self.assertEqual(result['imports_count'], 1)
                    self.assertFalse(result['summary_tail_verified'] or result['native_loaded'])

    def test_script_key_is_separate_and_unknown_tag_and_truncation_are_not_guessed(self):
        """脚本资源常量独立；错误标签/错用另一格式的密钥/截断分别拒绝，不猜明文。"""
        self.assertEqual(self.inspect(package_fixture(script=True, compressed=True))['root_packages'], ['Engine'])
        fixture = package_fixture()
        for data, reason in [(struct.pack('<I', 0x3e27a901) + fixture[4:], 'UNSUPPORTED_ENCRYPTED_PACKAGE_HEADER'),
                             (struct.pack('<I', 0x3e27a900) + fixture[4:], 'UNSUPPORTED_DECODED_PACKAGE_SUMMARY'),
                             (bytes(4) + fixture[4:], 'UNSUPPORTED_ENCRYPTED_PACKAGE_HEADER'),
                             (fixture[:135], 'PACKAGE_READ_OUT_OF_BOUNDS')]:
            with self.subTest(reason=reason), self.assertRaisesRegex(ValueError, reason):
                self.inspect(data)

    def test_import_reference_outside_table_rejected(self):
        """不存在的外层导入索引不能变成缺失包名或读取其他数据。"""
        with self.assertRaisesRegex(ValueError, 'PACKAGE_IMPORT_REFERENCE_INVALID'):
            self.inspect(package_fixture(outer=-2))

    def test_lzo_declared_output_and_corrupt_stream_rejected(self):
        """压缩器错误输出大小与损坏块必须失败，不能用截断数据继续构建目录。"""
        raw = package_fixture(compressed=True)
        for data in (raw[:-1], raw[:-4] + bytes(4)):
            with self.subTest(length=len(data)), self.assertRaises(ValueError):
                self.inspect(data)
        with tempfile.TemporaryFile() as stream:
            stream.write(bytes(24))
            with self.assertRaisesRegex(ValueError, 'UNSUPPORTED_LZO_CHUNK_HEADER'):
                decompress_chunk(stream, 0, 24, 1, 24)

    def test_missing_ambiguous_and_budget_pending_dependencies_remain_visible(self):
        """缺失、重名、遍历限额均保留；不会静默缩小依赖分母或宣布完整闭包。"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cooked = root / 'TGame/CookedPC'
            cooked.mkdir(parents=True)
            (cooked / 'Seed.upk').write_bytes(package_fixture())
            limited = inventory(root, ['Seed'], 1)
            self.assertEqual(limited['unexpanded_packages'], ['Engine'])
            self.assertFalse(limited['traversal_complete'] or limited['dependency_closure_verified'])
            missing = inventory(root, ['Seed'])
            self.assertEqual(missing['packages'][1]['status'], 'MISSING')
            (cooked / 'Engine.u').write_bytes(package_fixture())
            (cooked / 'Engine.upk').write_bytes(package_fixture())
            self.assertEqual(inventory(root, ['Seed'])['packages'][1]['status'], 'AMBIGUOUS')
