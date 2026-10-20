"""固定摘要跨系统部署回归；兼容换行不能让被修改的配置绕过完整性检查。"""
import hashlib
from pathlib import Path
import tempfile
from unittest import TestCase
from revival.frozen_config import frozen_bytes


class FrozenConfigTests(TestCase):
    """用独立 JSON 字节夹具验证历史指纹保留与内容篡改拒绝。"""
    def test_git_line_endings_preserve_original_digest_but_not_changed_content(self):
        """Git 转为 LF 后仍能还原受信 CRLF 摘要；字段变化或额外 CR 无法通过。"""
        original = b'{\r\n  "amount": 7\r\n}\r\n'
        digest = hashlib.sha256(original).hexdigest()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'frozen.json'
            path.write_bytes(original.replace(b'\r\n', b'\n'))
            self.assertEqual(hashlib.sha256(frozen_bytes(path, digest)).hexdigest(), digest)
            for changed in (original.replace(b'7', b'8'), original+b'\r'):
                path.write_bytes(changed)
                self.assertNotEqual(hashlib.sha256(frozen_bytes(path, digest)).hexdigest(), digest)
