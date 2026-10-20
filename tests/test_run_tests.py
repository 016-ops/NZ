"""验证测试入口在 Windows 常见的非 UTF-8 控制台下仍输出有效证据。"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class ConsoleReport(unittest.TestCase):
    """用真实子进程复现 cp1252 stdout，不依赖本机 Windows 或数据库。"""

    def test_chinese_skip_reason_survives_cp1252_stdout(self):
        """中文跳过原因写入 UTF-8 报告，并在 ASCII 安全的终端摘要中保留含义。"""
        root = Path(__file__).resolve().parents[1]
        reason = '仅在 Windows 可用，当前夹具主动跳过'
        with tempfile.TemporaryDirectory() as directory:
            fixture = Path(directory)
            (fixture / 'nz_runner_console_fixture.py').write_text(
                'import unittest\n'
                'class Fixture(unittest.TestCase):\n'
                f'    @unittest.skip({reason!r})\n'
                '    def test_skip(self):\n'
                '        pass\n',
                encoding='utf-8',
            )
            report_path = fixture / 'report.json'
            env = os.environ.copy()
            # 强制匹配 GitHub Windows runner 的管道编码；用独立临时模块保证
            # 不改变实际业务/工具测试集合，也不连接 MySQL。
            env['PYTHONIOENCODING'] = 'cp1252'
            env['PYTHONUTF8'] = '0'
            env['PYTHONPATH'] = str(fixture) + os.pathsep + env.get('PYTHONPATH', '')
            result = subprocess.run(
                [sys.executable, str(root / 'tools/run_tests.py'), '--tooling',
                 '--labels', 'nz_runner_console_fixture', '--output', str(report_path)],
                cwd=root, env=env, capture_output=True, timeout=30,
            )
            self.assertEqual(result.returncode, 0, result.stderr.decode('cp1252'))
            console = json.loads(result.stdout.decode('ascii'))
            saved = json.loads(report_path.read_text(encoding='utf-8'))
            self.assertEqual(console['skipped'][0]['reason'], reason)
            self.assertEqual(saved['skipped'][0]['reason'], reason)
            self.assertEqual(console['tests'], 1)
            self.assertEqual(saved['exit_code'], 0)
