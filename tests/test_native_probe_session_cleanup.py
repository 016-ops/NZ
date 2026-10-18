"""探针凭据撤销回读测试；区分明确AUTH拒绝与网络断开，不使用真实账号或邮件。"""
import unittest
from unittest.mock import Mock

from tools.email_launcher import LoginError
from tools.native_email_boot import revoke_owned_dev_session


class ProbeSessionCleanupTests(unittest.TestCase):
    """注销应答与来源/游戏凭据失效证据独立，不因连接错误误报清理完成。"""

    def test_logout_and_both_credentials_rejected(self):
        """正常注销后两个只读入口都AUTH拒绝，证明本次来源和游戏凭据失效。"""
        client=Mock()
        client.request.side_effect=[{'logged_out':True},LoginError('AUTH'),LoginError('AUTH')]
        result=revoke_owned_dev_session(client,'test-source',game_token='test-game',client_sha='a'*64)
        self.assertTrue(result['verified'] and result['logout_ack'])
        self.assertEqual(result['errors'],[])

    def test_lost_logout_reply_requires_both_rejection_readbacks(self):
        """注销提交后丢失应答可由两项明确拒绝确认，不能仅因写请求超时断定未提交。"""
        client=Mock()
        client.request.side_effect=[TimeoutError(),LoginError('AUTH'),LoginError('AUTH')]
        result=revoke_owned_dev_session(client,'test-source',game_token='test-game',client_sha='a'*64)
        self.assertTrue(result['verified'])
        self.assertFalse(result['logout_ack'])
        self.assertEqual(result['errors'],[{'step':'logout','type':'TimeoutError'}])

    def test_network_failure_or_still_usable_game_token_not_verified(self):
        """全断网和来源失效但游戏仍可读都失败，公开结果不含请求或凭据。"""
        for replies in [[ConnectionRefusedError()]*3,
                        [{'logged_out':True},LoginError('AUTH'),{'unexpected':'accepted'}],
                        [{'logged_out':True},LoginError('AUTH'),LoginError('EMAIL_NOT_VERIFIED')]]:
            with self.subTest(replies=len(replies)):
                client=Mock()
                client.request.side_effect=replies
                result=revoke_owned_dev_session(client,'test-source',game_token='test-game',client_sha='a'*64)
                self.assertFalse(result['verified'])
                self.assertNotIn('test-game',str(result))

    def test_pre_redeem_failure_only_requires_source_revocation(self):
        """尚未产生游戏凭据时只查来源失效，不访问不存在的游戏会话。"""
        client=Mock()
        client.request.side_effect=[{'logged_out':True},LoginError('AUTH')]
        result=revoke_owned_dev_session(client,'test-source')
        self.assertTrue(result['verified'])
        self.assertIsNone(result['game_rejected'])
        self.assertEqual(client.request.call_count,2)
