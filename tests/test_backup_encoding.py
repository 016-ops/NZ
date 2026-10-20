"""备份行规范化的独立编码验证；不执行SQL、不创建账号或读取真实玩家资产。"""
from datetime import date
import unittest

from revival.backup import canonical_row, normalize_restored_row
from revival.models import NativeCheckin, NativeRuleRevision


class BackupEncodingTests(unittest.TestCase):
    """原生驱动与ORM仅允许等价类型表示，日期、JSON及二进制的实际内容仍严格比较。"""

    def test_json_text_boolean_and_scalar_match_orm_without_ignoring_changed_values(self):
        """恢复JSON文本/布尔数值后与ORM相同；修改嵌套内容仍必须产生不同摘要正文。"""
        expected = {'enabled': True, 'definition': {'items': [1, True, None], 'name': '中文'}}
        restored = normalize_restored_row(NativeRuleRevision,
            {'enabled': 1, 'definition': '{"items":[1,true,null],"name":"中文"}'})
        self.assertEqual(canonical_row(expected), canonical_row(restored))
        changed = normalize_restored_row(NativeRuleRevision,
            {'enabled': 1, 'definition': '{"items":[2,true,null],"name":"中文"}'})
        self.assertNotEqual(canonical_row(expected), canonical_row(changed))
        scalar = normalize_restored_row(NativeRuleRevision, {'definition': '"plain-string"'})
        self.assertEqual(scalar['definition'], 'plain-string')
        with self.assertRaises(ValueError):
            normalize_restored_row(NativeRuleRevision, {'definition': 'damaged-json'})

    def test_date_binary_and_null_preserve_values_during_canonical_encoding(self):
        """真实DateField可参与摘要，BLOB不同容器等价；日期或字节改变不能被吞掉。"""
        expected = {'day': date(2026, 10, 19), 'result': {'reward': 100001}}
        restored = normalize_restored_row(NativeCheckin,
            {'day': date(2026, 10, 19), 'result': '{"reward":100001}'})
        self.assertEqual(canonical_row(expected), canonical_row(restored))
        restored['day'] = date(2026, 10, 20)
        self.assertNotEqual(canonical_row(expected), canonical_row(restored))
        self.assertEqual(canonical_row({'payload': b'\x00\xff', 'empty': None}),
                         canonical_row({'payload': memoryview(b'\x00\xff'), 'empty': None}))
        self.assertNotEqual(canonical_row({'payload': b'\x00\xff'}), canonical_row({'payload': b'\x00\xfe'}))
