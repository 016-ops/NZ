"""迁移专项验收：仅源格式使用 SQLite，导入目标与业务行为全部在真实 MySQL 执行。"""
import sqlite3
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from django.db import connections, DatabaseError
from revival.db import Repository
from revival.importer import IMPORT_ORDER, import_sqlite
from revival.models import DeploymentLock, SchemaVersion, TABLE_MODELS
from revival.security import otp
from revival.service import Service, Fault
from .test_control_plane import Base


class MigrationTests(Base):
    """从只读 SQLite 源导入真实 MySQL，核对身份、安全状态、资产及幂等记录。"""
    def legacy(self, *, empty_target=True):
        """从真实 ORM 状态生成旧格式快照；内部新增主键由 Repository 自动排除。"""
        source = Path(self.directory.name) / 'legacy.db'
        schema = Path(__file__).parent / 'fixtures/legacy-schema.sql'
        with self.s.db.transaction() as repository:
            data = {name: repository.select(name) for name in TABLE_MODELS}
        with closing(sqlite3.connect(source)) as legacy:
            legacy.executescript(schema.read_text(encoding='utf-8'))
            for name in ['deployment_metadata', *IMPORT_ORDER]:
                for row in data[name]:
                    columns = list(row)
                    legacy.execute(f"INSERT INTO {name} ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})", list(row.values()))
            legacy.commit()
        if empty_target:
            self._fixture_teardown()
            DeploymentLock.objects.create(pk=1)
            SchemaVersion.objects.create(version=1)
        return source

    def test_import_preserves_identity_unicode_assets_and_replays(self):
        """验证场景：迁移后保留账号 ID、中文角色、安全状态、账本和幂等重放。"""
        account, player = self.player('migrated-user',character=False)
        self.command('character.create',{'nickname':'迁移测试角色'},player)
        blocked, _ = self.player('blocked-user')
        self.command('sanction.create',{'target_account_id':blocked})
        data = self.meta(target_account_id=account, catalog_version='test-only-v1', changes=[{'type':'currency','code':'GP','delta':100}])
        first = self.s.command(self.owner, 'asset.grant', data)
        source = self.legacy()
        original = source.read_bytes()
        report = import_sqlite(source, test_profile=True)
        self.assertTrue(report['verified']); self.assertEqual(report['wallet_balance'],100)
        self.assertEqual(source.read_bytes(), original)
        self.s = Service(test_profile=True,clock=lambda:self.now)
        self.assertEqual(self.s.command(self.owner, 'asset.grant', data), first)
        self.assertEqual(self.balance(account),100)
        self.assertEqual(self.s.query(player,'me')['character']['nickname'],'迁移测试角色')
        with self.assertRaises(Fault): self.s.login('blocked-user',self.password)
        self.now += 30
        self.assertTrue(self.s.login('owner',self.password,kind='admin',otp_code=otp(self.admin['otp_secret'],self.now//30))['token'])
        self.assertTrue(self.s.login('migrated-user',self.password)['token'])

    def test_dry_run_and_duplicate_import_are_safe(self):
        """验证场景：dry-run 无写入，完成后的重复导入被拒绝。"""
        source = self.legacy()
        report = import_sqlite(source, dry_run=True, test_profile=True)
        self.assertTrue(report['dry_run'])
        self.assertTrue(report['verified'])
        self.assertTrue(all(not TABLE_MODELS[name].objects.exists() for name in IMPORT_ORDER))
        # 源库即便曾绕过 CHECK，也必须在 dry-run 时遭目标 MySQL 拒绝，且不遗留已插入行。
        with closing(sqlite3.connect(source)) as legacy:
            legacy.execute('PRAGMA ignore_check_constraints=ON')
            legacy.execute("UPDATE admins SET role='invalid'");legacy.commit()
        with self.assertRaises(DatabaseError): import_sqlite(source,dry_run=True,test_profile=True)
        self.assertTrue(all(not TABLE_MODELS[name].objects.exists() for name in IMPORT_ORDER))
        with closing(sqlite3.connect(source)) as legacy:
            legacy.execute("UPDATE admins SET role='owner'");legacy.commit()
        import_sqlite(source, test_profile=True)
        with self.assertRaises(ValueError): import_sqlite(source, test_profile=True)

    def test_nonempty_target_and_wrong_profile_are_rejected(self):
        """验证场景：目标非空或 profile 不符时拒绝导入且不改变目标。"""
        source = self.legacy(empty_target=False)
        with self.assertRaises(ValueError): import_sqlite(source, test_profile=True)
        with self.assertRaises(ValueError): import_sqlite(source, test_profile=False)

    def test_mid_import_failure_rolls_back_all_business_rows(self):
        """验证场景：导入中途异常回滚所有已插入业务表。"""
        self.player()
        source = self.legacy()
        original = Repository.insert
        def fail(repository, name, values, **kwargs):
            """在指定导入步骤注入异常，验证所有业务记录和元数据全部回滚。"""
            if name=='characters': raise RuntimeError('injected import failure')
            return original(repository,name,values,**kwargs)
        with patch.object(Repository,'insert',fail):
            with self.assertRaises(RuntimeError): import_sqlite(source,test_profile=True)
        self.assertTrue(all(not TABLE_MODELS[name].objects.exists() for name in IMPORT_ORDER))

    def test_source_schema_and_foreign_keys_are_checked(self):
        """验证场景：未知源版本、列结构不符和孤立外键均拒绝导入。"""
        source = self.legacy()
        with closing(sqlite3.connect(source)) as legacy:
            legacy.execute('ALTER TABLE admins ADD COLUMN unsupported TEXT');legacy.commit()
        with self.assertRaises(ValueError): import_sqlite(source,test_profile=True)

    def test_source_orphan_foreign_key_is_rejected(self):
        """旧 SQLite 的连接可能未开启外键，导入必须主动检查，不能把孤儿记录带入 MySQL。"""
        source = self.legacy()
        with closing(sqlite3.connect(source)) as legacy:
            legacy.execute("INSERT INTO characters VALUES('orphan','missing','孤儿角色','READY','test-only-v1')")
            legacy.commit()
        with self.assertRaises(ValueError): import_sqlite(source,test_profile=True)

    def test_unsupported_legacy_version_is_rejected(self):
        """未知旧库版本必须拒绝，不能猜测字段映射或绕过本次 v1 导入合同。"""
        source = self.legacy()
        with closing(sqlite3.connect(source)) as legacy:
            legacy.execute('PRAGMA ignore_check_constraints=ON')
            legacy.execute('UPDATE schema_version SET version=2');legacy.commit()
        with self.assertRaises(ValueError): import_sqlite(source,test_profile=True)

    def test_case_sensitive_and_chinese_nickname_constraints(self):
        """验证场景：MySQL 二进制排序保留昵称大小写和中文唯一语义。"""
        first, token_a = self.player('case-a',character=False)
        second, token_b = self.player('case-b',character=False)
        self.command('character.create',{'nickname':'测试角色A'},token_a)
        self.command('character.create',{'nickname':'测试角色a'},token_b)
        third, token_c = self.player('case-c',character=False)
        with self.assertRaises(Fault): self.command('character.create',{'nickname':'测试角色A'},token_c)
        self.assertEqual(self.s.query(token_b,'me')['character']['nickname'],'测试角色a')

    def test_concurrent_same_key_commits_only_once(self):
        """验证场景：并发相同幂等键只提交一次，返回同一业务结果。"""
        from .support import DatabasePool
        account, _ = self.player()
        data=self.meta(target_account_id=account,catalog_version='test-only-v1',changes=[{'type':'currency','code':'GP','delta':10}])
        with DatabasePool(max_workers=6) as pool:
            results=list(pool.map(lambda _: self.s.command(self.owner,'asset.grant',data),range(6)))
        self.assertTrue(all(result==results[0] for result in results))
        self.assertEqual(self.balance(account),10)
        self.assertEqual(TABLE_MODELS['asset_operations'].objects.count(),1)
