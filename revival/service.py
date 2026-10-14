"""本地账号、管理员、权限和审计服务；真实游戏协议保持独立且未接入。"""
from __future__ import annotations

from django.db.models import F, Q

import base64
import json
import os
import hmac
import secrets
from django.db import IntegrityError
from django.db import connections
import time
import unicodedata
import uuid
from pathlib import Path

from .db import Database
from .security import new_token, normalize_login, password_hash, token_hash, verify_otp, verify_password


class Fault(Exception):
    """携带既有错误码、消息和 HTTP 状态的业务异常，由 HTTP 边界统一转换响应。"""
    def __init__(self, code, message, status=400):
        """初始化当前对象依赖和状态；参数仅保存在对象中，具体持久化由显式事务入口处理。"""
        self.code, self.message, self.status = code, message, status
        super().__init__(message)


# 稳定序列化 JSON，用作幂等摘要、冻结快照和流水详情；禁止非有限数字，避免跨请求摘要不一致。
def canonical(value):
    """稳定序列化 JSON，用作幂等摘要、冻结快照和流水详情；禁止非有限数字，避免跨请求摘要不一致。"""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


# 生成本地业务随机 ID；保持旧格式的 UUID 十六进制字符串，以便旧库导入及接口兼容。
def identifier():
    """生成本地业务随机 ID；保持旧格式的 UUID 十六进制字符串，以便旧库导入及接口兼容。"""
    return uuid.uuid4().hex


# 严格拒绝布尔值冒充整数，并检查业务上下界，防止溢出、负余额或不合法数量。
def integer(value, minimum, maximum, name):
    """严格拒绝布尔值冒充整数，并检查业务上下界，防止溢出、负余额或不合法数量。"""
    if type(value) is not int or not minimum <= value <= maximum:
        raise Fault('INVALID', f'{name} must be an integer between {minimum} and {maximum}')
    return value


# 限制长度并拒绝控制字符；此公共校验用于昵称、操作原因、业务 key 和请求元数据。
def text_field(value, name, limit=200):
    """限制长度并拒绝控制字符；此公共校验用于昵称、操作原因、业务 key 和请求元数据。"""
    if not isinstance(value, str) or not value.strip() or len(value) > limit or any(ord(c) < 32 for c in value):
        raise Fault('INVALID', f'Invalid {name}')
    return value.strip()


PERMISSIONS = {
    'owner': {'*'},
    'users': {'user.read', 'user.approve', 'user.reset', 'invite.create', 'session.revoke', 'sanction.create', 'audit.read'},
    'resources': {'user.read', 'asset.read', 'asset.grant', 'asset.preview', 'audit.read'},
    'auditor': {'user.read', 'asset.read', 'audit.read'},
}

COMMANDS = {
    'invite.create': 'invite.create', 'invite.revoke': 'invite.create',
    'user.approve': 'user.approve', 'user.disable': 'user.approve', 'user.activate': 'user.approve',
    'user.reset': 'user.reset', 'session.revoke': 'session.revoke',
    'sanction.create': 'sanction.create', 'sanction.revoke': 'sanction.create',
    'character.create': 'player', 'room.create': 'player', 'room.join': 'player',
    'room.leave': 'player', 'room.ready': 'player', 'room.configure': 'player', 'room.start': 'player',
    'room.cancel': 'player', 'asset.grant': 'asset.grant', 'asset.compensate': '*',
    'asset.batch': '*', 'shop.purchase': 'player', 'inventory.consume': 'player',
    'inventory.equip': 'player',
}


class Service:
    """组织账号、权限、幂等和审计事务；账号、房间、资产共享同一 MySQL 部署锁。"""
    def _password_only_test_admin(self, admin):
        """仅登录时允许指定测试账号省略 MFA；账号 ID、数据库名和合成模式缺一不可。

        该例外由本地命令写入私有环境配置，客户端不能通过请求参数启用。
        高风险业务再认证不调用本例外，因此不会顺带放宽全部管理员操作的认证规则。
        """
        return (self.test_profile and admin['id']==os.environ.get('NZ_PASSWORD_ONLY_TEST_ADMIN_ID')
                and connections[self.db.alias].settings_dict['NAME']==os.environ.get('NZ_PASSWORD_ONLY_TEST_ADMIN_DATABASE'))

    def _fixed_test_otp(self, admin, code):
        """用户明确要求的单账号测试例外：ID、库名、合成 profile 三重限定，验证码只比对摘要。

        配置缺失、账号不同、切到当前客户端库或验证码错误时均不放行；正常 MFA 校验不受影响。
        固定码不消费 TOTP 时间步，因而允许该测试账号重复登录和再次确认高风险合成操作。
        """
        if not self.test_profile or admin['id'] != os.environ.get('NZ_FIXED_TEST_ADMIN_ID'):
            return False
        if connections[self.db.alias].settings_dict['NAME'] != os.environ.get('NZ_FIXED_TEST_ADMIN_DATABASE'):
            return False
        expected = os.environ.get('NZ_FIXED_TEST_ADMIN_OTP_HASH', '')
        if not isinstance(code, str) or len(code)!=6 or not code.isascii() or not code.isdigit():
            return False
        return bool(expected) and hmac.compare_digest(token_hash(code), expected)

    def __init__(self, database='default', *, test_profile=False, clock=None):
        """初始化当前对象依赖和状态；参数仅保存在对象中，具体持久化由显式事务入口处理。"""
        self.db = Database(database)
        self.clock = clock or (lambda: int(time.time()))
        self.test_profile = test_profile
        self.catalog = json.loads((Path(__file__).resolve().parents[1] / 'config/test-profile.json').read_text(encoding='utf-8')) if test_profile else {
            'id': 'cn-current-unverified', 'evidence': 'UNKNOWN', 'currencies': {}, 'items': {},
            'offers': {}, 'maps': {}, 'inventory_limit': 0,
        }
        with self.db.transaction() as c:
            previous = c.select('deployment_metadata', Q(key='profile'), columns=['value']).fetchone()
            if previous and previous['value'] != self.catalog['id']:
                raise Fault('PROFILE_CONFLICT', 'Test and current-client databases must be separate', 409)
            c.insert('deployment_metadata', {'key': 'profile', 'value': self.catalog['id']}, ignore=True)

    # 仅允许空管理员表创建首个 Owner；密码与随机 MFA 密钥生成后，在同一锁定事务写入账号和审计，密钥只在返回值中展示一次。
    def bootstrap_admin(self, login, password, role='owner'):
        """仅允许空管理员表创建首个 Owner；密码与随机 MFA 密钥生成后，在同一锁定事务写入账号和审计，密钥只在返回值中展示一次。"""
        if role not in PERMISSIONS:
            raise ValueError('Unknown role')
        hashed = password_hash(password)
        secret = base64.b32encode(secrets.token_bytes(20)).decode('ascii')
        admin_id = identifier()
        with self.db.transaction() as c:
            if c.select('admins', Q()).fetchone():
                raise Fault('BOOTSTRAPPED', 'Only the initial Owner can be bootstrapped', 409)
            if role != 'owner':
                raise Fault('FORBIDDEN', 'Initial administrator must be Owner', 403)
            c.insert('admins', {'id': admin_id, 'login': normalize_login(login), 'password_hash': hashed, 'otp_secret': secret, 'role': role})
            self._audit(c, admin_id, 'admin.bootstrap', admin_id, 'local-bootstrap', 'initial Owner', {})
        return {'admin_id': admin_id, 'otp_secret': secret}

    # 校验独立密码；管理员额外校验未消费的 TOTP 时间步，玩家校验批准状态与有效制裁。会话撤销、席位释放、新会话和审计必须一起提交。
    def login(self, login, password, *, kind='player', otp_code=None):
        """校验独立密码；管理员额外校验未消费的 TOTP 时间步，玩家校验批准状态与有效制裁。会话撤销、席位释放、新会话和审计必须一起提交。"""
        if kind not in ('player', 'admin'):
            raise Fault('INVALID', 'Unknown login kind')
        try:
            normalized = normalize_login(login)
        except ValueError:
            raise Fault('AUTH', 'Invalid credentials', 401)
        with self.db.transaction() as c:
            table = 'admins' if kind == 'admin' else 'accounts'
            row = c.select(table, Q(login=normalized)).fetchone()
            # Slow verification even for an unknown username.
            encoded = row['password_hash'] if row else DUMMY_PASSWORD_HASH
            if not verify_password(password, encoded) or row is None:
                raise Fault('AUTH', 'Invalid credentials', 401)
            if kind == 'admin':
                step = verify_otp(row['otp_secret'], otp_code, self.clock(), row['last_otp_step'])
                fixed_test_code = self._fixed_test_otp(row, otp_code)
                password_only_test = self._password_only_test_admin(row)
                if not row['active'] or (step is None and not fixed_test_code and not password_only_test):
                    raise Fault('AUTH', 'Invalid credentials', 401)
                if not fixed_test_code and not password_only_test:
                    c.update('admins', Q(id=row['id']), {'last_otp_step': step})
            else:
                self._active(c, row)
                c.update('sessions', (Q(subject_id=row['id']) & Q(kind=kind)), {'revoked': 1})
                self._detach(c, row['id'])
                c.update('accounts', Q(id=row['id']), {'last_login': self.clock()})
            token = new_token()
            c.insert('sessions', {'token_hash': token_hash(token), 'subject_id': row['id'], 'kind': kind, 'security_version': row['security_version'], 'expires_at': self.clock() + (900 if kind == 'admin' else 3600), 'revoked': 0})
            self._audit(c, row['id'], 'session.login', row['id'], identifier(), 'authenticated login', {'kind': kind})
            return {'token': token, 'subject_id': row['id'], 'kind': kind, 'expires_in': 900 if kind == 'admin' else 3600}

    # 邀请次数检查、玩家映射分配、账号创建与幂等结果共用部署锁。先检查同键结果，确保响应丢失重试不会再次消耗邀请。
    def register(self, data):
        """邀请次数检查、玩家映射分配、账号创建与幂等结果共用部署锁。先检查同键结果，确保响应丢失重试不会再次消耗邀请。"""
        request_id, key, reason = self._metadata(data)
        try:
            login = normalize_login(data.get('login'))
            hashed = password_hash(data.get('password'))
        except ValueError as e:
            raise Fault('INVALID', str(e))
        invitation = text_field(data.get('invitation'), 'invitation', 128)
        summary = {'login': login, 'password_digest': token_hash(data['password']), 'invite_hash': token_hash(invitation)}
        with self.db.transaction() as c:
            scope = 'register:' + login
            previous = self._prior(c, scope, key, summary)
            if previous is not None:
                return previous
            invite = c.select('invitations', Q(token_hash=token_hash(invitation))).fetchone()
            if not invite or invite['revoked'] or invite['expires_at'] <= self.clock() or invite['uses'] >= invite['max_uses']:
                raise Fault('INVITATION', 'Invitation is invalid, expired, revoked, or exhausted', 403)
            account_id = identifier()
            game_id = c.maximum('accounts', 'game_id', fallback=10000, increment=1).fetchone()[0]
            integer(game_id, 1, 2147483647, 'local game mapping')
            try:
                c.insert('accounts', {'id': account_id, 'game_id': game_id, 'login': login, 'password_hash': hashed, 'status': 'PENDING_REVIEW', 'created_at': self.clock()})
            except IntegrityError:
                raise Fault('CONFLICT', 'Login is already registered', 409)
            c.update('invitations', Q(id=invite['id']), {'uses': F('uses') + 1})
            result = {'account_id': account_id, 'status': 'PENDING_REVIEW', 'game_id': game_id, 'mapping_evidence': 'LOCAL_EQUIVALENT'}
            self._audit(c, account_id, 'account.register', account_id, request_id, reason, {'invitation_id': invite['id']})
            self._remember(c, scope, key, summary, result)
            return result

    # 每次授权重新检查账号状态与所有未过期制裁；某条封禁撤销不等于其他封禁也失效。
    def _active(self, c, row):
        """每次授权重新检查账号状态与所有未过期制裁；某条封禁撤销不等于其他封禁也失效。"""
        if row['status'] != 'ACTIVE':
            raise Fault('ACCOUNT_STATE', 'Account must be approved and active', 403)
        if c.select('sanctions', (((Q(account_id=row['id']) & Q(revoked=0)) & Q(starts_at__lte=self.clock())) & (Q(expires_at__isnull=True) | Q(expires_at__gt=self.clock())))).fetchone():
            raise Fault('SANCTION', 'Account has an active sanction', 403)

    # 令牌仅以 SHA-256 摘要查表；检查期限、安全版本、玩家制裁或管理员状态后，再校验操作权限，读取接口也必须经过此入口。
    def _actor(self, c, token, permission=None):
        """令牌仅以 SHA-256 摘要查表；检查期限、安全版本、玩家制裁或管理员状态后，再校验操作权限，读取接口也必须经过此入口。"""
        if not isinstance(token, str):
            raise Fault('AUTH', 'Authentication required', 401)
        session = c.select('sessions', Q(token_hash=token_hash(token))).fetchone()
        if not session or session['revoked'] or session['expires_at'] <= self.clock():
            raise Fault('AUTH', 'Session is expired or revoked', 401)
        kind = session['kind']
        table = 'admins' if kind == 'admin' else 'accounts'
        row = c.select(table, Q(id=session['subject_id'])).fetchone()
        if row is None or row['security_version'] != session['security_version']:
            raise Fault('AUTH', 'Session security version changed', 401)
        if kind == 'player':
            self._active(c, row)
        elif not row['active']:
            raise Fault('AUTH', 'Administrator is disabled', 401)
        if permission == 'player' and kind != 'player':
            raise Fault('FORBIDDEN', 'Player session required', 403)
        if permission and permission != 'player':
            if kind != 'admin' or (permission not in PERMISSIONS[row['role']] and '*' not in PERMISSIONS[row['role']]):
                raise Fault('FORBIDDEN', 'Permission denied', 403)
        return {'id': row['id'], 'kind': kind, 'role': row['role'] if kind == 'admin' else 'player'}

    # 要求请求追踪 ID、幂等键和原因均为有界非空文本，避免无限长审计值或无法归因的写操作。
    def _metadata(self, data):
        """要求请求追踪 ID、幂等键和原因均为有界非空文本，避免无限长审计值或无法归因的写操作。"""
        return (text_field(data.get('request_id'), 'request_id', 128),
                text_field(data.get('idempotency_key'), 'idempotency_key', 128),
                text_field(data.get('reason'), 'reason', 500))

    # 只追加安全摘要；审计与业务共享当前事务，审计写入失败必须使资产或账号改动回滚。
    def _audit(self, c, actor, action, target, request_id, reason, detail, result='COMMITTED'):
        """只追加安全摘要；审计与业务共享当前事务，审计写入失败必须使资产或账号改动回滚。"""
        c.insert('audit_events', {'actor': actor, 'action': action, 'target': target, 'request_id': request_id, 'time': self.clock(), 'result': result, 'reason': reason, 'detail': canonical(detail)})

    # 比较操作者 scope 与幂等键对应的规范化请求摘要；同键内容不同返回 409，同键相同返回原结果。
    def _prior(self, c, scope, key, data):
        """比较操作者 scope 与幂等键对应的规范化请求摘要；同键内容不同返回 409，同键相同返回原结果。"""
        digest = token_hash(canonical(data))
        previous = c.select('requests', (Q(scope=scope) & Q(key=key))).fetchone()
        if previous:
            if previous['payload_hash'] != digest:
                raise Fault('IDEMPOTENCY_CONFLICT', 'Same key has different content', 409)
            return json.loads(previous['result'])
        return None

    # 仅保存安全业务响应和请求摘要；邀请明文、节点令牌与密码不会出现在持久化结果中。
    def _remember(self, c, scope, key, data, result):
        """仅保存安全业务响应和请求摘要；邀请明文、节点令牌与密码不会出现在持久化结果中。"""
        c.insert('requests', {'scope': scope, 'key': key, 'payload_hash': token_hash(canonical(data)), 'result': canonical(result)})

    # 把待同步事件加入当前业务事务，event_key 唯一；真实客户端未确认之前保持 PENDING，不把本地提交当作同步成功。
    def _outbox(self, c, key, event_type, account_id, payload):
        """把待同步事件加入当前业务事务，event_key 唯一；真实客户端未确认之前保持 PENDING，不把本地提交当作同步成功。"""
        c.insert('outbox', {'event_key': key, 'type': event_type, 'account_id': account_id, 'payload': canonical(payload)})

    # 先认证授权，再读取幂等结果；高风险命令消耗新的 MFA 时间步，随后分派账号、房间或资产业务，最终一起写审计和幂等结果。
    def command(self, token, action, data):
        """先认证授权，再读取幂等结果；高风险命令消耗新的 MFA 时间步，随后分派账号、房间或资产业务，最终一起写审计和幂等结果。"""
        if not isinstance(action, str) or action not in COMMANDS:
            raise Fault('NOT_FOUND', 'Unknown command', 404)
        request_id, key, reason = self._metadata(data)
        summary = {k: v for k, v in data.items() if k not in ('request_id', 'idempotency_key')}
        # Passwords are never persisted in an idempotency result or audit.
        summary = dict(summary, action=action)
        with self.db.transaction() as c:
            actor = self._actor(c, token, COMMANDS[action])
            scope = actor['kind'] + ':' + actor['id']
            previous = self._prior(c, scope, key, summary)
            if previous is not None:
                return previous
            changes = data.get('changes') if isinstance(data.get('changes'), list) else []
            high_risk = action in ('asset.batch', 'asset.compensate') or (
                action == 'asset.grant' and sum(abs(x['delta']) for x in changes
                                              if isinstance(x, dict) and x.get('type') == 'currency'
                                              and type(x.get('delta')) is int) > 10000)
            if high_risk and actor['kind'] == 'admin' and actor['role'] == 'owner':
                self._step_up(c, actor, data)
            if action.startswith('room.'):
                from .rooms import room_command
                result = room_command(self, c, actor, action, data)
            elif action.startswith(('asset.', 'shop.', 'inventory.')):
                from .assets import asset_command
                result = asset_command(self, c, actor, action, data)
            else:
                result = self._account_command(c, actor, action, data)
            target = data.get('target_account_id', actor['id'])
            safe_detail = {k: v for k, v in result.items() if k not in ('invitation_token', 'node_token')}
            self._audit(c, actor['id'], action, target, request_id, reason, safe_detail)
            self._remember(c, scope, key, summary, safe_detail)
            return result

    # Owner 大额发放、批量与补偿必须再次提供当前密码和新 TOTP；消费时间步与业务共享事务，失败不会留下半完成状态。
    def _step_up(self, c, actor, data):
        """Owner 大额发放、批量与补偿必须再次提供当前密码和新 TOTP；消费时间步与业务共享事务，失败不会留下半完成状态。"""
        admin = c.select('admins', Q(id=actor['id'])).fetchone()
        step = verify_otp(admin['otp_secret'], data.get('otp_code'), self.clock(), admin['last_otp_step'])
        fixed_test_code = self._fixed_test_otp(admin, data.get('otp_code'))
        if (step is None and not fixed_test_code) or not verify_password(data.get('current_password'), admin['password_hash']):
            raise Fault('REAUTH_REQUIRED', 'High-risk operation requires current Owner password and a fresh MFA code', 403)
        if not fixed_test_code:
            c.update('admins', Q(id=actor['id']), {'last_otp_step': step})

    # HTTP 拒绝发生在业务事务回滚之后，用独立事务追加脱敏拒绝审计，不记录令牌、密码或驱动异常文本。
    def record_rejection(self, action, request_id, code):
        """HTTP 拒绝发生在业务事务回滚之后，用独立事务追加脱敏拒绝审计，不记录令牌、密码或驱动异常文本。"""
        with self.db.transaction() as c:
            self._audit(c, 'anonymous-or-rejected-session', str(action)[:80], None, str(request_id)[:128],
                        'Rejected HTTP action', {'error': str(code)[:80]}, result='REJECTED')

    # 查询业务账号并统一转换缺失记录为 NOT_FOUND，避免各业务模块产生不一致的空记录行为。
    def _account(self, c, account_id):
        """查询业务账号并统一转换缺失记录为 NOT_FOUND，避免各业务模块产生不一致的空记录行为。"""
        row = c.select('accounts', Q(id=account_id)).fetchone()
        if row is None:
            raise Fault('NOT_FOUND', 'Account does not exist', 404)
        return row

    # 提升安全版本、撤销全部玩家会话、释放房间并取消当前局奖励资格；安全 outbox 与这些状态变化必须一起提交。
    def _revoke(self, c, account_id, key):
        """提升安全版本、撤销全部玩家会话、释放房间并取消当前局奖励资格；安全 outbox 与这些状态变化必须一起提交。"""
        c.update('accounts', Q(id=account_id), {'security_version': F('security_version') + 1})
        c.update('sessions', (Q(subject_id=account_id) & Q(kind='player')), {'revoked': 1})
        self._detach(c, account_id)
        self._outbox(c, key, 'security.revoked', account_id, {'account_id': account_id})

    # 只操作本地房间与奖励资格；节点真实下线仍需 transport 确认，不能将此数据库操作解释为原客户端已断开。
    def _detach(self, c, account_id):
        # Local control state is authoritative; a retail node still needs acknowledged delivery.
        """只操作本地房间与奖励资格；节点真实下线仍需 transport 确认，不能将此数据库操作解释为原客户端已断开。"""
        c.update('match_players', Q(account_id=account_id, match_id__in=c.table('matches').filter(state__in=['STARTING','ACTIVE']).values('id')), {'eligible': 0})
        member = c.select('room_members', Q(account_id=account_id), columns=['room_id']).fetchone()
        if member:
            from .rooms import leave_room
            leave_room(self, c, account_id, member['room_id'])

    # 实现邀请、角色初始化、审批、停用、重置和制裁；保留原状态转换和权限边界，外层负责统一幂等和审计。
    def _account_command(self, c, actor, action, data):
        """实现邀请、角色初始化、审批、停用、重置和制裁；保留原状态转换和权限边界，外层负责统一幂等和审计。"""
        if action == 'invite.create':
            uses = integer(data.get('max_uses'), 1, 20, 'max_uses')
            expiry = integer(data.get('expires_at'), self.clock() + 1, self.clock() + 2592000, 'expires_at')
            token, invite_id = new_token(), identifier()
            c.insert('invitations', {'id': invite_id, 'token_hash': token_hash(token), 'max_uses': uses, 'expires_at': expiry})
            return {'invitation_id': invite_id, 'invitation_token': token, 'expires_at': expiry, 'max_uses': uses}
        if action == 'invite.revoke':
            cursor = c.update('invitations', Q(id=data.get('invitation_id')), {'revoked': 1})
            if not cursor.rowcount:
                raise Fault('NOT_FOUND', 'Invitation does not exist', 404)
            return {'invitation_id': data['invitation_id'], 'revoked': True}
        if action == 'character.create':
            nickname = unicodedata.normalize('NFC', text_field(data.get('nickname'), 'nickname', 24))
            if len(nickname.encode('utf-8')) > 48 or any(unicodedata.category(x).startswith('C') or x in '<>"\'' for x in nickname):
                raise Fault('INVALID', 'Nickname violates local web rules; retail nickname rules remain unknown')
            old = c.select('characters', Q(account_id=actor['id'])).fetchone()
            if old:
                if old['nickname'] != nickname:
                    raise Fault('CONFLICT', 'Character already initialized with a different nickname', 409)
                return {'character_id': old['id'], 'state': 'READY', 'evidence': 'LOCAL_EQUIVALENT'}
            character_id = identifier()
            try:
                c.insert('characters', {'id': character_id, 'account_id': actor['id'], 'nickname': nickname, 'init_state': 'READY', 'profile': self.catalog['id']})
            except IntegrityError:
                raise Fault('CONFLICT', 'Nickname is already used', 409)
            for currency in self.catalog['currencies']:
                c.insert('wallets', {'account_id': actor['id'], 'currency': currency, 'balance': 0})
            return {'character_id': character_id, 'state': 'READY', 'evidence': 'LOCAL_EQUIVALENT', 'starter_assets': 'NONE'}
        if action == 'sanction.revoke':
            row = c.select('sanctions', Q(id=data.get('sanction_id'))).fetchone()
            if row is None:
                raise Fault('NOT_FOUND', 'Sanction does not exist', 404)
            c.update('sanctions', Q(id=row['id']), {'revoked': 1})
            return {'sanction_id': row['id'], 'remaining_sanctions_checked_on_every_request': True}
        account = self._account(c, data.get('target_account_id'))
        account_id = account['id']
        if action in ('user.approve', 'user.activate', 'user.disable'):
            state = 'DISABLED' if action == 'user.disable' else 'ACTIVE'
            if action == 'user.approve' and account['status'] not in ('PENDING_REVIEW', 'ACTIVE'):
                raise Fault('CONFLICT', 'Use activate for a disabled account', 409)
            c.update('accounts', Q(id=account_id), {'status': state})
            if state == 'DISABLED':
                self._revoke(c, account_id, 'disable:' + identifier())
            return {'account_id': account_id, 'status': state}
        if action == 'user.reset':
            try:
                hashed = password_hash(data.get('new_password'))
            except ValueError as e:
                raise Fault('INVALID', str(e))
            c.update('accounts', Q(id=account_id), {'password_hash': hashed})
            self._revoke(c, account_id, 'reset:' + identifier())
            return {'account_id': account_id, 'sessions_revoked': True}
        if action == 'session.revoke':
            self._revoke(c, account_id, 'kick:' + identifier())
            return {'account_id': account_id, 'local_sessions_revoked': True, 'retail_node_delivery': 'UNIMPLEMENTED'}
        if action == 'sanction.create':
            expires = data.get('expires_at')
            if expires is not None:
                integer(expires, self.clock() + 1, self.clock() + 315360000, 'expires_at')
            sanction_id = identifier()
            c.insert('sanctions', {'id': sanction_id, 'account_id': account_id, 'reason': data['reason'], 'starts_at': self.clock(), 'expires_at': expires, 'revoked': 0})
            self._revoke(c, account_id, 'sanction:' + sanction_id)
            return {'sanction_id': sanction_id, 'account_id': account_id, 'local_sessions_revoked': True, 'retail_node_delivery': 'UNIMPLEMENTED'}
        raise Fault('NOT_FOUND', 'Unimplemented action', 404)

    # 按视图执行服务端权限检查和有界分页；只暴露旧接口允许的逻辑列，资源/用户管理员只读自己的审计记录。
    def query(self, token, view, **filters):
        """按视图执行服务端权限检查和有界分页；只暴露旧接口允许的逻辑列，资源/用户管理员只读自己的审计记录。"""
        with self.db.transaction() as c:
            if view == 'me':
                actor = self._actor(c, token)
                if actor['kind'] == 'admin':
                    return {'id': actor['id'], 'role': actor['role'], 'kind': 'admin'}
                return self._user_view(c, actor['id'])
            if view in ('catalog', 'rooms'):
                self._actor(c, token)
                if view == 'catalog':
                    return self.catalog
                return [dict(r) for r in c.select('rooms', ~Q(state='CLOSED'), order=('id',), limit=100)]
            permission = 'asset.read' if view in ('assets', 'operations', 'outbox') else 'audit.read' if view == 'audit' else 'user.read'
            actor = self._actor(c, token, permission)
            limit = integer(filters.get('limit', 50), 1, 100, 'limit')
            offset = integer(filters.get('offset', 0), 0, 1000000, 'offset')
            if view == 'users':
                search = filters.get('search', '')
                if not isinstance(search, str) or len(search) > 100:
                    raise Fault('INVALID', 'Invalid search')
                # Literal substring search rather than user-controlled SQL wildcard matching.
                rows = c.select('accounts', Q(login__contains=search.lower()), columns=['id', 'game_id', 'login', 'status', 'created_at', 'last_login'], order=('created_at', 'id'), limit=limit, offset=offset)
                return [dict(r) for r in rows]
            if view in ('user', 'assets'):
                return self._user_view(c, filters.get('account_id'))
            if view == 'audit':
                if actor['role'] in ('users', 'resources'):
                    rows = c.select('audit_events', Q(actor=actor['id']), order=('-id',), limit=limit, offset=offset)
                else:
                    rows = c.select('audit_events', Q(), order=('-id',), limit=limit, offset=offset)
                return [dict(r) for r in rows]
            if view == 'invitations':
                self._actor(c, token, 'invite.create')
                return [dict(r) for r in c.select('invitations', Q(), columns=['id', 'max_uses', 'uses', 'expires_at', 'revoked'], order=('-expires_at',), limit=limit)]
            if view == 'operations':
                rows = c.select('asset_operations', Q(account_id=filters.get('account_id')), order=('-created_at', 'id'), limit=limit)
                results = [dict(r) for r in rows]
                for result in results:
                    result['entries'] = [dict(r) for r in c.entries(result['id'])]
                return results
            if view == 'outbox':
                return [dict(r) for r in c.select('outbox', Q(), columns=['id', 'type', 'account_id', 'status', 'attempts', 'last_error'], order=('-id',), limit=limit)]
            if view == 'dashboard':
                return {'accounts': c.count('accounts', Q()).fetchone()[0],
                        'active_sessions': c.count('sessions', (Q(revoked=0) & Q(expires_at__gt=self.clock()))).fetchone()[0],
                        'rooms': c.count('rooms', ~Q(state='CLOSED')).fetchone()[0],
                        'pending_sync': c.count('outbox', ~Q(status='DELIVERED')).fetchone()[0],
                        'profile': self.catalog['id'], 'retail_compatible': False}
            raise Fault('NOT_FOUND', 'Unknown view', 404)

    # 组合脱敏账号、角色、钱包、库存、装备、制裁及席位；密码哈希、MFA 密钥与会话令牌不得进入读取模型。
    def _user_view(self, c, account_id):
        """组合脱敏账号、角色、钱包、库存、装备、制裁及席位；密码哈希、MFA 密钥与会话令牌不得进入读取模型。"""
        row = self._account(c, account_id)
        result = {key: row[key] for key in ('id', 'login', 'game_id', 'status', 'created_at', 'last_login')}
        result['character'] = dict(character) if (character := c.select('characters', Q(account_id=account_id)).fetchone()) else None
        result['wallets'] = [dict(r) for r in c.select('wallets', Q(account_id=account_id), columns=['currency', 'balance', 'version'])]
        result['inventory'] = [dict(r) for r in c.select('inventory', Q(account_id=account_id))]
        result['equipment'] = [dict(r) for r in c.select('equipment', Q(account_id=account_id), columns=['slot', 'inventory_id'])]
        result['sanctions'] = [dict(r) for r in c.select('sanctions', Q(account_id=account_id))]
        result['room'] = dict(member) if (member := c.select('room_members', Q(account_id=account_id)).fetchone()) else None
        result['retail_sync'] = 'UNIMPLEMENTED'
        return result


# Fixed salt and digest are deliberately invalid credentials but incur the same KDF work.
DUMMY_PASSWORD_HASH = 'scrypt$32768$8$3$' + '00' * 16 + '$' + '00' * 64
