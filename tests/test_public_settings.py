"""公网配置启动门禁：HTTPS来源必须配套私有密钥，不能把本机研究默认值带入发布。"""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class PublicSettingsTests(unittest.TestCase):
    """在隔离子进程导入settings，不连接MySQL，不读取真实配置或输出任何配置值。"""

    def load_settings(self, key):
        """固定空dotenv与人工密钥夹具，返回导入退出码；驱动输出保留在内存不回显。"""
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory)/'empty.env'
            config.touch()
            environment = dict(os.environ, NZ_ENV_FILE=str(config), NZ_MAIL_ENV_FILE=str(config),
                NZ_SECRET_KEY=key, NZ_PUBLIC_ORIGIN='https://portal.example.test')
            return subprocess.run([sys.executable, '-c', 'import nzproject.settings'], env=environment,
                capture_output=True, cwd=Path(__file__).resolve().parents[1], timeout=10).returncode

    def test_empty_short_and_known_default_secret_rejected(self):
        """显式公网域名配合缺失、短值或公开默认常量时均不能启动。"""
        for value in ('', 'short-fixture', 'local-control-plane-no-cookie-sessions'):
            with self.subTest(kind='default' if value.startswith('local-') else 'short'):
                self.assertNotEqual(self.load_settings(value), 0)

    def test_explicit_private_length_fixture_allows_public_settings(self):
        """符合强度长度的人工占位值通过配置加载；测试不宣称随机度或真实远端部署。"""
        self.assertEqual(self.load_settings('isolated-public-settings-fixture-'+'x'*32), 0)
