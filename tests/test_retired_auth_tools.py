"""旧DEV认证脚本必须在任何私有文件读取或连接之前明确退役，纯内存验证。"""
from importlib import import_module
from unittest import TestCase
from unittest.mock import patch


class RetiredAuthTools(TestCase):
    """阻止历史运行时TOTP/免码凭据被工具继续读取或当成有效登录方式。"""
    def test_legacy_workflows_fail_before_private_io(self):
        """导入后直接调用入口，监视所有路径读取和网络连接，失败码不包含凭据。"""
        for name in ('provision_dev_owner', 'verify_dev', 'verify_email_dev', 'mail_acceptance'):
            module = import_module('tools.' + name)
            with self.subTest(tool=name), patch('pathlib.Path.read_text', side_effect=AssertionError('private read')), \
                    patch('pathlib.Path.read_bytes', side_effect=AssertionError('private read')), \
                    patch('socket.create_connection', side_effect=AssertionError('network must remain unused')):
                with self.assertRaisesRegex(RuntimeError, '^AUTH_METHOD_RETIRED:'):
                    module.main()

    def test_old_owner_login_never_accesses_credentials_or_sends_request(self):
        """旧owner_login保留导入名称兼容，但不能从传入记录取出秘密或访问HTTP。"""
        from tools.verify_email_dev import owner_login
        with self.assertRaisesRegex(RuntimeError, '^AUTH_METHOD_RETIRED:'):
            owner_login(None, None)
