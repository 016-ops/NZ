"""Launcher Django hook公共依赖回归；不导入PyInstaller或项目设置，不连接数据库。"""
import ast
from importlib.util import find_spec
from pathlib import Path
import subprocess
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]


def hook_assignment(name):
    """只读hook的声明AST，避免执行collect_data_files或任何PyInstaller收集逻辑。"""
    tree = ast.parse((ROOT / 'tools/launcher_hooks/hook-django.py').read_text(encoding='utf-8'))
    for statement in tree.body:
        if isinstance(statement, ast.Assign) and any(
                isinstance(target, ast.Name) and target.id == name for target in statement.targets):
            return statement.value
    raise AssertionError('Missing explicit hook assignment: ' + name)


class LauncherDjangoHookTests(unittest.TestCase):
    """核对上游启动依赖与模板资源，单独证明最小WSGI设置无需业务应用或数据库。"""

    def test_debug_engine_dynamic_libraries_are_explicit_public_hidden_imports(self):
        """Django views.debug导入期间加载i18n；hook须包含其公共动态模块。"""
        hidden = ast.literal_eval(hook_assignment('hiddenimports'))
        self.assertIn('django.templatetags.i18n', hidden)
        spec = find_spec('django.views.debug')
        self.assertIsNotNone(spec)
        upstream = ast.parse(Path(spec.origin).read_text(encoding='utf-8'))
        debug_engine = next(statement.value for statement in upstream.body
                            if isinstance(statement, ast.Assign) and any(
                                isinstance(target, ast.Name) and target.id == 'DEBUG_ENGINE'
                                for target in statement.targets))
        libraries = ast.literal_eval(next(keyword.value for keyword in debug_engine.keywords
                                         if keyword.arg == 'libraries'))
        self.assertEqual(libraries['i18n'], 'django.templatetags.i18n')
        self.assertTrue(set(libraries.values()).issubset(hidden))
        for module in libraries.values():
            with self.subTest(module=module):
                self.assertIsNotNone(find_spec(module))

    def test_debug_templates_are_covered_by_public_data_pattern(self):
        """保留上游异常页目录及technical_500文本/HTML资源，验证相对收集模式命中。"""
        data = hook_assignment('datas')
        patterns = ast.literal_eval(next(keyword.value for keyword in data.keywords
                                         if keyword.arg == 'includes'))
        self.assertIn('views/templates/**', patterns)
        django_root = Path(find_spec('django').origin).parent
        template_directory = django_root / 'views/templates'
        self.assertTrue(template_directory.is_dir())
        files = {item.relative_to(django_root).as_posix() for item in template_directory.rglob('*')
                 if item.is_file()}
        required = {'views/templates/technical_500.html', 'views/templates/technical_500.txt',
                    'views/templates/technical_404.html', 'views/templates/default_urlconf.html'}
        self.assertTrue(required.issubset(files))
        collected = {item.relative_to(django_root).as_posix()
                     for pattern in patterns for item in django_root.glob(pattern)
                     if item.is_file()}
        self.assertTrue(required.issubset(collected))

    def test_minimal_wsgi_startup_has_no_project_settings_or_database_backend(self):
        """隔离子进程仅在内存配置Launcher同等设置，验证WSGI初始化与默认dummy配置。"""
        script = '''
import sys
from django.conf import settings
settings.configure(DEBUG=False, SECRET_KEY='public-test-only', ALLOWED_HOSTS=['127.0.0.1'],
    ROOT_URLCONF='public_launcher_hook_test', INSTALLED_APPS=[], MIDDLEWARE=[],
    DATA_UPLOAD_MAX_MEMORY_SIZE=8192)
from django.core.wsgi import get_wsgi_application
get_wsgi_application()
from django.db import connections
assert connections.settings['default']['ENGINE'] == 'django.db.backends.dummy'
assert 'django.templatetags.i18n' in sys.modules
assert 'django.db.backends.dummy.base' not in sys.modules
assert 'nzproject.settings' not in sys.modules
assert 'PyInstaller' not in sys.modules
'''
        result = subprocess.run([sys.executable, '-I', '-c', script], capture_output=True,
                                text=True, timeout=30, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()
