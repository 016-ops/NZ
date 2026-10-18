"""Windows原生房间重试日志的DPAPI实测；只用临时虚拟口令，不操作任何真实账号。"""
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from tools.native_feature_bridge import NativeFeatureBridge


@unittest.skipUnless(os.name=='nt','DPAPI仅适用于Windows桌面启动器')
class NativeRoomJournalTests(unittest.TestCase):
    """同用户恢复、篡改拒绝、旧无口令日志兼容三种独立场景。"""

    def bridge(self,path):
        """构造不会访问网络/数据库的桥接外壳，验证仅本次日志文件的存取。"""
        return NativeFeatureBridge(SimpleNamespace(client=None,scope={},game_id=10001),path)

    def test_secret_is_protected_and_same_user_can_resume(self):
        """明文短口令不出现在磁盘，同一Windows用户恢复得到相同请求，清理只删本文件。"""
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'operation.json';other=Path(directory)/'keep.txt';other.write_text('keep')
            bridge=self.bridge(path)
            value=dict(game_id=10001,fingerprint='fixture',request=dict(action='room.create',password='12345'))
            bridge.save_pending(value);raw=path.read_text()
            self.assertNotIn('12345',raw);self.assertEqual(json.loads(raw)['version'],'native-command-dpapi-v1')
            restored=self.bridge(path);self.assertEqual(restored.pending,value)
            restored.clear_pending();self.assertFalse(path.exists());self.assertTrue(other.exists())

    def test_corrupted_protected_journal_is_preserved_and_rejected(self):
        """密文被篡改不能变成空口令或新请求；保留文件供明确恢复。"""
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'operation.json';bridge=self.bridge(path)
            bridge.save_pending(dict(game_id=10001,request=dict(action='room.join',password='12345')))
            value=json.loads(path.read_text());value['protected']='AAAA'
            path.write_text(json.dumps(value));before=path.read_bytes()
            with self.assertRaises((RuntimeError,ValueError)):self.bridge(path)
            self.assertEqual(path.read_bytes(),before)

    def test_previous_passwordless_journal_remains_compatible(self):
        """已有商城/工程无口令日志不强制迁移，保证历史未知提交仍可恢复。"""
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'operation.json'
            value=dict(game_id=10001,fingerprint='old',request=dict(action='shop.purchase',idempotency_key='old-key'))
            path.write_text(json.dumps(value));self.assertEqual(self.bridge(path).pending,value)


if __name__=='__main__':unittest.main()
