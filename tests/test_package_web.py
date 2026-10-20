"""Web发布归档白名单验证：逐文件摘要可复核，受保护配置、链接及运行数据不能混入。"""
import hashlib
import json
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch

from tools.package_web import collect, package


class WebPackage(unittest.TestCase):
    """用临时最小发布树验证安全边界，不连接数据库、不读取真实运行配置。"""

    def setUp(self):
        """建立打包器必需文件及应排除的runtime/.env文件，内容均为人工测试数据。"""
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for name in ('admin/dist/index.html', 'revival/views.py', 'manage.py', 'requirements.txt',
                'requirements-monitoring.txt', 'pyproject.toml', 'tools/monitor_agent.py',
                'docs/WEB_DEPLOYMENT.md', 'runtime/private-fixture.json', '.env.local'):
            path = self.root/name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('fixture', encoding='utf-8')

    def test_manifest_matches_archive_and_excludes_runtime(self):
        """归档只含白名单，每个清单SHA与实际内容一致；敏感运行目录永不入包。"""
        destination = self.root/'output/web.tar.gz'
        with patch('tools.package_web.subprocess.check_output', return_value='a'*40):
            result = package(self.root, destination)
        self.assertEqual(result['sha256'], hashlib.sha256(destination.read_bytes()).hexdigest())
        with tarfile.open(destination) as archive:
            manifest = json.load(archive.extractfile('RELEASE.json'))
            names = set(archive.getnames())
            self.assertNotIn('runtime/private-fixture.json', names)
            self.assertNotIn('.env.local', names)
            self.assertNotIn('tools/dev_install.sh', names)
            for row in manifest['files']:
                content = archive.extractfile(row['path']).read()
                self.assertEqual(hashlib.sha256(content).hexdigest(), row['sha256'])
                self.assertEqual(len(content), row['bytes'])

    def test_private_file_inside_whitelist_rejected(self):
        """即使出现在允许目录，环境文件仍导致整体拒绝，不能静默发布秘密配置。"""
        (self.root/'revival/.env.production').write_text('fixture', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'PRIVATE_FILE'):
            collect(self.root)

    def test_private_suffixes_and_named_credentials_are_rejected_even_under_allowed_directories(self):
        """monitor.env等不以点开头的配置、证书私钥容器和数据库旁文件均导致整体拒绝。"""
        for name in ('monitor.env','user-mysql.env','smtp.env.production','player.sqlite3',
                     'identity.p12','identity.ppk','cache.db-wal','cache.db-shm','id_rsa','credentials'):
            with self.subTest(name=name):
                path = self.root/'config'/name
                path.parent.mkdir(exist_ok=True)
                path.write_text('synthetic-private-fixture-not-a-real-credential', encoding='utf-8')
                try:
                    with self.assertRaisesRegex(ValueError, 'PRIVATE_FILE'):
                        collect(self.root)
                finally:
                    path.unlink()

    def test_link_directory_rejected(self):
        """允许目录中的目录链接同样拒绝，防止链接目标绕过归档白名单。"""
        (self.root/'revival/linked').symlink_to(self.root/'runtime', target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'LINK_REJECTED'):
            collect(self.root)


if __name__ == '__main__':
    unittest.main()
