"""原生研究配置的持久恢复守卫；调用方先验证独立工作副本，本模块只管理显式登记的配置。"""
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile

from tools.client_workspace import checked_path, digest


class ConfigConflict(RuntimeError):
    """目标已被其他操作修改；保留现有内容，不用研究变体或旧备份覆盖它。"""


def _stage_bytes(target, content):
    """在目标同目录写入并同步完整临时文件；失败只清理本次创建的临时文件。"""
    name = None
    try:
        with tempfile.NamedTemporaryFile(mode='wb', prefix='.nz-config-', dir=target.parent, delete=False) as stream:
            name = Path(stream.name)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        return name
    except BaseException:
        if name is not None:
            name.unlink(missing_ok=True)
        raise


def _atomic_replace(target, content, expected_sha256=None):
    """完整临时文件落盘后再次核对目标摘要，再原子替换，避免部分写入损坏当前配置。"""
    temporary = _stage_bytes(target, content)
    try:
        if expected_sha256 is not None and digest(target) != expected_sha256:
            raise ConfigConflict('Target changed before atomic replacement')
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


class NativeConfigGuard:
    """先保存原件和恢复计划，再替换配置；逐项恢复互不阻塞，外部新内容必须保留。

    apply(relative, bytes) 只接受工作副本内普通文件；restore() 返回逐项状态。
    from_plan(root, output) 用于中断后恢复。所有原件备份放在调用方提供的私人证据目录。
    """

    def __init__(self, root, output):
        """建立独占的新恢复目录；拒绝根目录重解析点及客户端内部的证据输出目录。"""
        self._paths(root, output)
        self.store.mkdir()
        self.records = []
        self.plan_errors = []
        self._persist()

    def _paths(self, root, output):
        """确定受限工作根和恢复根；调用方仍负责原件/工作副本登记与客户端版本校验。"""
        root, output = Path(root).absolute(), Path(output).absolute()
        checked_path(root, '.')
        checked_path(output, '.')
        self.root, self.output = root.resolve(strict=True), output.resolve(strict=True)
        if not self.root.is_dir() or not self.output.is_dir() or self.output.is_relative_to(self.root):
            raise ValueError('Recovery output must be an existing directory outside the client workspace')
        self.store = checked_path(self.output, 'native-config-guard')
        self.plan_path = self.store / 'plan.json'

    @classmethod
    def from_plan(cls, root, output):
        """把恢复计划视为不可信数据，校验版本、根绑定、相对路径和备份文件名后才允许恢复。"""
        instance = cls.__new__(cls)
        instance._paths(root, output)
        plan_path = checked_path(instance.store, 'plan.json')
        if plan_path.stat().st_size > 256 * 1024:
            raise ValueError('Recovery plan is too large')
        payload = json.loads(plan_path.read_text(encoding='utf-8'))
        if not isinstance(payload, dict) or payload.get('version') != 1 or payload.get('workspace') != str(instance.root):
            raise ValueError('Recovery plan version or workspace mismatch')
        records = payload.get('records')
        if not isinstance(records, list) or len(records) > 32:
            raise ValueError('Invalid recovery record list')
        seen = set()
        for record in records:
            if not isinstance(record, dict) or not isinstance(record.get('relative'), str):
                raise ValueError('Invalid recovery record')
            target = checked_path(instance.root, record['relative'])
            canonical = target.relative_to(instance.root).as_posix()
            # 计划只接受本模块输出的规范相对路径，拒绝别名重复、Windows ADS 和尾部点空格。
            if canonical != record['relative'] or canonical == '.' or any(
                    ':' in part or part.rstrip(' .') != part for part in Path(canonical).parts):
                raise ValueError('Noncanonical recovery target')
            if canonical.casefold() in seen:
                raise ValueError('Duplicate recovery target')
            seen.add(canonical.casefold())
            if not isinstance(record.get('backup'), str) or not re.fullmatch(r'\d{3}\.original\.bin', record['backup']):
                raise ValueError('Invalid recovery backup name')
            checked_path(instance.store, record['backup'])
            for name in ('original_sha256', 'variant_sha256'):
                if not isinstance(record.get(name), str) or not re.fullmatch('[0-9a-f]{64}', record[name]):
                    raise ValueError('Invalid recovery digest')
        instance.records = records
        instance.plan_errors = []
        return instance

    def _persist(self):
        """恢复计划自身也先写完整临时文件再替换；原件备份已同步后才登记 PREPARED。"""
        target = checked_path(self.store, 'plan.json')
        data = {'version': 1, 'workspace': str(self.root), 'records': self.records}
        _atomic_replace(target, json.dumps(data, ensure_ascii=False, indent=2).encode('utf-8'))

    def apply(self, relative, variant):
        """保存原件、持久 PREPARED 计划后安装变体；返回不含配置正文的记录，失败向调用方抛出。"""
        if not isinstance(variant, bytes) or len(variant) > 4 * 1024 * 1024:
            raise ValueError('Configuration variant must be bytes within 4 MiB')
        target = checked_path(self.root, relative)
        relative = target.relative_to(self.root).as_posix()
        if any(':' in part or part.rstrip(' .') != part for part in Path(relative).parts):
            raise ValueError('Unsupported configuration path')
        if len(self.records) >= 32 or any(row['relative'].casefold() == relative.casefold() for row in self.records):
            raise ValueError('Too many or duplicate configuration targets')
        if not target.is_file() or target.stat().st_size > 4 * 1024 * 1024:
            raise ValueError('Expected an existing bounded configuration file')
        original = target.read_bytes()
        backup_name = f'{len(self.records):03d}.original.bin'
        backup = checked_path(self.store, backup_name)
        with backup.open('xb') as stream:
            stream.write(original)
            stream.flush()
            os.fsync(stream.fileno())
        original_sha = hashlib.sha256(original).hexdigest()
        if digest(backup) != original_sha:
            raise ValueError('Recovery backup verification failed')
        record = {'relative': relative, 'backup': backup_name, 'original_sha256': original_sha,
                  'variant_sha256': hashlib.sha256(variant).hexdigest(), 'status': 'PREPARED', 'errors': []}
        self.records.append(record)
        self._persist()
        try:
            _atomic_replace(target, variant, expected_sha256=original_sha)
            if digest(target) != record['variant_sha256']:
                raise ConfigConflict('Installed configuration differs from the prepared variant')
            record['status'] = 'APPLIED'
            self._persist()
        except BaseException as error:
            record['status'] = 'APPLY_FAILED'
            record['errors'].append({'phase': 'apply', 'type': type(error).__name__})
            try:
                self._persist()
            except Exception as persistence_error:
                self.plan_errors.append({'phase': 'apply-plan', 'type': type(persistence_error).__name__})
            raise
        return dict(record)

    def restore(self):
        """逐项比较并恢复原件；某项丢失、备份损坏或写失败不阻止其他项，也不覆盖外部新内容。"""
        for record in reversed(self.records):
            record['errors'] = []
            try:
                target = checked_path(self.root, record['relative'])
                current = digest(target)
                if current == record['original_sha256']:
                    record['status'] = 'ALREADY_ORIGINAL'
                elif current != record['variant_sha256']:
                    record['status'] = 'EXTERNAL_CHANGE_PRESERVED'
                    record['errors'].append({'phase': 'restore', 'type': 'ConfigConflict'})
                else:
                    backup = checked_path(self.store, record['backup'])
                    if not backup.is_file() or backup.stat().st_size > 4 * 1024 * 1024:
                        raise ValueError('Expected a bounded recovery backup')
                    original = backup.read_bytes()
                    if hashlib.sha256(original).hexdigest() != record['original_sha256']:
                        raise ValueError('Recovery backup digest mismatch')
                    _atomic_replace(target, original, expected_sha256=record['variant_sha256'])
                    if digest(target) != record['original_sha256']:
                        raise ConfigConflict('Restored configuration digest mismatch')
                    record['status'] = 'RESTORED'
            except Exception as error:
                record['status'] = 'EXTERNAL_CHANGE_PRESERVED' if isinstance(error, ConfigConflict) else 'RESTORE_ERROR'
                record['errors'].append({'phase': 'restore', 'type': type(error).__name__})
            try:
                self._persist()
            except Exception as error:
                self.plan_errors.append({'phase': 'restore-plan', 'type': type(error).__name__})
        return {'configuration_restored': all(row['status'] in ('RESTORED', 'ALREADY_ORIGINAL') for row in self.records),
                'configurations': [dict(row) for row in self.records], 'plan_errors': list(self.plan_errors)}
