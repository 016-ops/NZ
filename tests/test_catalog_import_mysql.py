"""目录导入器的 SQL 边界回归；业务数据库集成及重复导入另有实际执行证据。"""
from pathlib import Path
import tempfile
from unittest import TestCase
from tools.catalog_mysql_import import seed_groups, statements


class CatalogImportParsingTests(TestCase):
    """防止字符串内分号再次破坏校验 SQL，以及非法种子绕过生成器白名单。"""

    def test_semicolon_inside_quoted_note_is_not_a_statement_boundary(self):
        """真实首轮失败中的说明字符串必须保持为一个 SELECT。"""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'verify.sql'
            path.write_text("-- generated\nSET @snapshot_id='x';\nSELECT 'MySQL; static evidence';", encoding='utf-8')
            self.assertEqual(statements(path), ["SET @snapshot_id='x';", "SELECT 'MySQL; static evidence';"])

    def test_seed_groups_keep_whitelisted_rows_and_reject_arbitrary_sql(self):
        """相邻同列 INSERT 可合并，额外命令及过大单行均在发送数据库前拒绝。"""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'seed.sql'
            sql = "INSERT INTO nzcat_snapshots (`snapshot_id`) VALUES (X'00') ON DUPLICATE KEY UPDATE `snapshot_id`=nzcat_snapshots.`snapshot_id`;\n"
            path.write_text(sql * 2, encoding='utf-8')
            groups = list(seed_groups(path, 1024))
            self.assertEqual(len(groups), 1)
            self.assertEqual(groups[0][2], 2)
            with self.assertRaises(ValueError):
                list(seed_groups(path, 10))
            path.write_text(sql + 'DROP TABLE players;\n', encoding='utf-8')
            with self.assertRaises(ValueError):
                list(seed_groups(path, 1024))
