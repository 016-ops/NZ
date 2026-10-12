"""Transactional local account/admin control plane. Retail protocol is a separate gate."""
from __future__ import annotations

import base64
import json
import secrets
import sqlite3
import time
import unicodedata
import uuid
from pathlib import Path

from .db import Database
from .security import new_token, normalize_login, password_hash, token_hash, verify_otp, verify_password


class Fault(Exception):
    def __init__(self, code, message, status=400):
        self.code, self.message, self.status = code, message, status
        super().__init__(message)


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def identifier():
    return uuid.uuid4().hex


def integer(value, minimum, maximum, name):
    if type(value) is not int or not minimum <= value <= maximum:
        raise Fault('INVALID', f'{name} must be an integer between {minimum} and {maximum}')
    return value


def text_field(value, name, limit=200):
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
    def __init__(self, database, *, test_profile=False, clock=None):
        self.db = Database(database)
        self.clock = clock or (lambda: int(time.time()))
        self.test_profile = test_profile
        self.catalog = json.loads((Path(__file__).resolve().parents[1] / 'config/test-profile.json').read_text(encoding='utf-8')) if test_profile else {
            'id': 'cn-current-unverified', 'evidence': 'UNKNOWN', 'currencies': {}, 'items': {},
            'offers': {}, 'maps': {}, 'inventory_limit': 0,
        }
        with self.db.transaction() as c:
            previous = c.execute("SELECT value FROM deployment_metadata WHERE key='profile'").fetchone()
            if previous and previous['value'] != self.catalog['id']:
                raise Fault('PROFILE_CONFLICT', 'Test and current-client databases must be separate', 409)
            c.execute("INSERT OR IGNORE INTO deployment_metadata VALUES('profile',?)", (self.catalog['id'],))

    def bootstrap_admin(self, login, password, role='owner'):
        if role not in PERMISSIONS:
            raise ValueError('Unknown role')
        hashed = password_hash(password)
        secret = base64.b32encode(secrets.token_bytes(20)).decode('ascii')
        admin_id = identifier()
        with self.db.transaction() as c:
            if c.execute('SELECT 1 FROM admins').fetchone():
                raise Fault('BOOTSTRAPPED', 'Only the initial Owner can be bootstrapped', 409)
            if role != 'owner':
                raise Fault('FORBIDDEN', 'Initial administrator must be Owner', 403)
            c.execute('INSERT INTO admins(id,login,password_hash,otp_secret,role) VALUES(?,?,?,?,?)',
                      (admin_id, normalize_login(login), hashed, secret, role))
            self._audit(c, admin_id, 'admin.bootstrap', admin_id, 'local-bootstrap', 'initial Owner', {})
        return {'admin_id': admin_id, 'otp_secret': secret}

    def login(self, login, password, *, kind='player', otp_code=None):
        if kind not in ('player', 'admin'):
            raise Fault('INVALID', 'Unknown login kind')
        try:
            normalized = normalize_login(login)
        except ValueError:
            raise Fault('AUTH', 'Invalid credentials', 401)
        with self.db.transaction() as c:
            table = 'admins' if kind == 'admin' else 'accounts'
            row = c.execute(f'SELECT * FROM {table} WHERE login=?', (normalized,)).fetchone()
            # Slow verification even for an unknown username.
            encoded = row['password_hash'] if row else DUMMY_PASSWORD_HASH
            if not verify_password(password, encoded) or row is None:
                raise Fault('AUTH', 'Invalid credentials', 401)
            if kind == 'admin':
                step = verify_otp(row['otp_secret'], otp_code, self.clock(), row['last_otp_step'])
                if not row['active'] or step is None:
                    raise Fault('AUTH', 'Invalid credentials', 401)
                c.execute('UPDATE admins SET last_otp_step=? WHERE id=?', (step, row['id']))
            else:
                self._active(c, row)
                c.execute('UPDATE sessions SET revoked=1 WHERE subject_id=? AND kind=?', (row['id'], kind))
                self._detach(c, row['id'])
                c.execute('UPDATE accounts SET last_login=? WHERE id=?', (self.clock(), row['id']))
            token = new_token()
            c.execute('INSERT INTO sessions VALUES(?,?,?,?,?,0)',
                      (token_hash(token), row['id'], kind, row['security_version'], self.clock() + (900 if kind == 'admin' else 3600)))
            self._audit(c, row['id'], 'session.login', row['id'], identifier(), 'authenticated login', {'kind': kind})
            return {'token': token, 'subject_id': row['id'], 'kind': kind, 'expires_in': 900 if kind == 'admin' else 3600}

    def register(self, data):
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
            invite = c.execute('SELECT * FROM invitations WHERE token_hash=?', (token_hash(invitation),)).fetchone()
            if not invite or invite['revoked'] or invite['expires_at'] <= self.clock() or invite['uses'] >= invite['max_uses']:
                raise Fault('INVITATION', 'Invitation is invalid, expired, revoked, or exhausted', 403)
            account_id = identifier()
            game_id = c.execute('SELECT COALESCE(MAX(game_id),10000)+1 FROM accounts').fetchone()[0]
            integer(game_id, 1, 2147483647, 'local game mapping')
            try:
                c.execute('INSERT INTO accounts(id,game_id,login,password_hash,status,created_at) VALUES(?,?,?,?,?,?)',
                          (account_id, game_id, login, hashed, 'PENDING_REVIEW', self.clock()))
            except sqlite3.IntegrityError:
                raise Fault('CONFLICT', 'Login is already registered', 409)
            c.execute('UPDATE invitations SET uses=uses+1 WHERE id=?', (invite['id'],))
            result = {'account_id': account_id, 'status': 'PENDING_REVIEW', 'game_id': game_id, 'mapping_evidence': 'LOCAL_EQUIVALENT'}
            self._audit(c, account_id, 'account.register', account_id, request_id, reason, {'invitation_id': invite['id']})
            self._remember(c, scope, key, summary, result)
            return result

    def _active(self, c, row):
        if row['status'] != 'ACTIVE':
            raise Fault('ACCOUNT_STATE', 'Account must be approved and active', 403)
        if c.execute('SELECT 1 FROM sanctions WHERE account_id=? AND revoked=0 AND starts_at<=? '
                     'AND (expires_at IS NULL OR expires_at>?)', (row['id'], self.clock(), self.clock())).fetchone():
            raise Fault('SANCTION', 'Account has an active sanction', 403)

    def _actor(self, c, token, permission=None):
        if not isinstance(token, str):
            raise Fault('AUTH', 'Authentication required', 401)
        session = c.execute('SELECT * FROM sessions WHERE token_hash=?', (token_hash(token),)).fetchone()
        if not session or session['revoked'] or session['expires_at'] <= self.clock():
            raise Fault('AUTH', 'Session is expired or revoked', 401)
        kind = session['kind']
        table = 'admins' if kind == 'admin' else 'accounts'
        row = c.execute(f'SELECT * FROM {table} WHERE id=?', (session['subject_id'],)).fetchone()
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

    def _metadata(self, data):
        return (text_field(data.get('request_id'), 'request_id', 128),
                text_field(data.get('idempotency_key'), 'idempotency_key', 128),
                text_field(data.get('reason'), 'reason', 500))

    def _audit(self, c, actor, action, target, request_id, reason, detail, result='COMMITTED'):
        c.execute('INSERT INTO audit_events(actor,action,target,request_id,time,result,reason,detail) VALUES(?,?,?,?,?,?,?,?)',
                  (actor, action, target, request_id, self.clock(), result, reason, canonical(detail)))

    def _prior(self, c, scope, key, data):
        digest = token_hash(canonical(data))
        previous = c.execute('SELECT * FROM requests WHERE scope=? AND key=?', (scope, key)).fetchone()
        if previous:
            if previous['payload_hash'] != digest:
                raise Fault('IDEMPOTENCY_CONFLICT', 'Same key has different content', 409)
            return json.loads(previous['result'])
        return None

    def _remember(self, c, scope, key, data, result):
        c.execute('INSERT INTO requests VALUES(?,?,?,?)', (scope, key, token_hash(canonical(data)), canonical(result)))

    def _outbox(self, c, key, event_type, account_id, payload):
        c.execute('INSERT INTO outbox(event_key,type,account_id,payload) VALUES(?,?,?,?)',
                  (key, event_type, account_id, canonical(payload)))

    def command(self, token, action, data):
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

    def _step_up(self, c, actor, data):
        admin = c.execute('SELECT * FROM admins WHERE id=?', (actor['id'],)).fetchone()
        step = verify_otp(admin['otp_secret'], data.get('otp_code'), self.clock(), admin['last_otp_step'])
        if step is None or not verify_password(data.get('current_password'), admin['password_hash']):
            raise Fault('REAUTH_REQUIRED', 'High-risk operation requires current Owner password and a fresh MFA code', 403)
        c.execute('UPDATE admins SET last_otp_step=? WHERE id=?', (step, actor['id']))

    def record_rejection(self, action, request_id, code):
        with self.db.transaction() as c:
            self._audit(c, 'anonymous-or-rejected-session', str(action)[:80], None, str(request_id)[:128],
                        'Rejected HTTP action', {'error': str(code)[:80]}, result='REJECTED')

    def _account(self, c, account_id):
        row = c.execute('SELECT * FROM accounts WHERE id=?', (account_id,)).fetchone()
        if row is None:
            raise Fault('NOT_FOUND', 'Account does not exist', 404)
        return row

    def _revoke(self, c, account_id, key):
        c.execute('UPDATE accounts SET security_version=security_version+1 WHERE id=?', (account_id,))
        c.execute("UPDATE sessions SET revoked=1 WHERE subject_id=? AND kind='player'", (account_id,))
        self._detach(c, account_id)
        self._outbox(c, key, 'security.revoked', account_id, {'account_id': account_id})

    def _detach(self, c, account_id):
        # Local control state is authoritative; a retail node still needs acknowledged delivery.
        c.execute('UPDATE match_players SET eligible=0 WHERE account_id=? AND match_id IN '
                  "(SELECT id FROM matches WHERE state IN ('STARTING','ACTIVE'))", (account_id,))
        member = c.execute('SELECT room_id FROM room_members WHERE account_id=?', (account_id,)).fetchone()
        if member:
            from .rooms import leave_room
            leave_room(self, c, account_id, member['room_id'])

    def _account_command(self, c, actor, action, data):
        if action == 'invite.create':
            uses = integer(data.get('max_uses'), 1, 20, 'max_uses')
            expiry = integer(data.get('expires_at'), self.clock() + 1, self.clock() + 2592000, 'expires_at')
            token, invite_id = new_token(), identifier()
            c.execute('INSERT INTO invitations(id,token_hash,max_uses,expires_at) VALUES(?,?,?,?)',
                      (invite_id, token_hash(token), uses, expiry))
            return {'invitation_id': invite_id, 'invitation_token': token, 'expires_at': expiry, 'max_uses': uses}
        if action == 'invite.revoke':
            cursor = c.execute('UPDATE invitations SET revoked=1 WHERE id=?', (data.get('invitation_id'),))
            if not cursor.rowcount:
                raise Fault('NOT_FOUND', 'Invitation does not exist', 404)
            return {'invitation_id': data['invitation_id'], 'revoked': True}
        if action == 'character.create':
            nickname = unicodedata.normalize('NFC', text_field(data.get('nickname'), 'nickname', 24))
            if len(nickname.encode('utf-8')) > 48 or any(unicodedata.category(x).startswith('C') or x in '<>"\'' for x in nickname):
                raise Fault('INVALID', 'Nickname violates local web rules; retail nickname rules remain unknown')
            old = c.execute('SELECT * FROM characters WHERE account_id=?', (actor['id'],)).fetchone()
            if old:
                if old['nickname'] != nickname:
                    raise Fault('CONFLICT', 'Character already initialized with a different nickname', 409)
                return {'character_id': old['id'], 'state': 'READY', 'evidence': 'LOCAL_EQUIVALENT'}
            character_id = identifier()
            try:
                c.execute('INSERT INTO characters VALUES(?,?,?,?,?)', (character_id, actor['id'], nickname, 'READY', self.catalog['id']))
            except sqlite3.IntegrityError:
                raise Fault('CONFLICT', 'Nickname is already used', 409)
            for currency in self.catalog['currencies']:
                c.execute('INSERT INTO wallets(account_id,currency,balance) VALUES(?,?,0)', (actor['id'], currency))
            return {'character_id': character_id, 'state': 'READY', 'evidence': 'LOCAL_EQUIVALENT', 'starter_assets': 'NONE'}
        if action == 'sanction.revoke':
            row = c.execute('SELECT * FROM sanctions WHERE id=?', (data.get('sanction_id'),)).fetchone()
            if row is None:
                raise Fault('NOT_FOUND', 'Sanction does not exist', 404)
            c.execute('UPDATE sanctions SET revoked=1 WHERE id=?', (row['id'],))
            return {'sanction_id': row['id'], 'remaining_sanctions_checked_on_every_request': True}
        account = self._account(c, data.get('target_account_id'))
        account_id = account['id']
        if action in ('user.approve', 'user.activate', 'user.disable'):
            state = 'DISABLED' if action == 'user.disable' else 'ACTIVE'
            if action == 'user.approve' and account['status'] not in ('PENDING_REVIEW', 'ACTIVE'):
                raise Fault('CONFLICT', 'Use activate for a disabled account', 409)
            c.execute('UPDATE accounts SET status=? WHERE id=?', (state, account_id))
            if state == 'DISABLED':
                self._revoke(c, account_id, 'disable:' + identifier())
            return {'account_id': account_id, 'status': state}
        if action == 'user.reset':
            try:
                hashed = password_hash(data.get('new_password'))
            except ValueError as e:
                raise Fault('INVALID', str(e))
            c.execute('UPDATE accounts SET password_hash=? WHERE id=?', (hashed, account_id))
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
            c.execute('INSERT INTO sanctions VALUES(?,?,?,?,?,0)', (sanction_id, account_id, data['reason'], self.clock(), expires))
            self._revoke(c, account_id, 'sanction:' + sanction_id)
            return {'sanction_id': sanction_id, 'account_id': account_id, 'local_sessions_revoked': True, 'retail_node_delivery': 'UNIMPLEMENTED'}
        raise Fault('NOT_FOUND', 'Unimplemented action', 404)

    def query(self, token, view, **filters):
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
                return [dict(r) for r in c.execute("SELECT * FROM rooms WHERE state!='CLOSED' ORDER BY id LIMIT 100")]
            permission = 'asset.read' if view in ('assets', 'operations', 'outbox') else 'audit.read' if view == 'audit' else 'user.read'
            actor = self._actor(c, token, permission)
            limit = integer(filters.get('limit', 50), 1, 100, 'limit')
            offset = integer(filters.get('offset', 0), 0, 1000000, 'offset')
            if view == 'users':
                search = filters.get('search', '')
                if not isinstance(search, str) or len(search) > 100:
                    raise Fault('INVALID', 'Invalid search')
                # Literal substring search rather than user-controlled SQL wildcard matching.
                rows = c.execute('SELECT id,game_id,login,status,created_at,last_login FROM accounts '
                                 'WHERE instr(login,?)>0 ORDER BY created_at,id LIMIT ? OFFSET ?', (search.lower(), limit, offset))
                return [dict(r) for r in rows]
            if view in ('user', 'assets'):
                return self._user_view(c, filters.get('account_id'))
            if view == 'audit':
                if actor['role'] in ('users', 'resources'):
                    rows = c.execute('SELECT * FROM audit_events WHERE actor=? ORDER BY id DESC LIMIT ? OFFSET ?', (actor['id'], limit, offset))
                else:
                    rows = c.execute('SELECT * FROM audit_events ORDER BY id DESC LIMIT ? OFFSET ?', (limit, offset))
                return [dict(r) for r in rows]
            if view == 'invitations':
                self._actor(c, token, 'invite.create')
                return [dict(r) for r in c.execute('SELECT id,max_uses,uses,expires_at,revoked FROM invitations ORDER BY expires_at DESC LIMIT ?', (limit,))]
            if view == 'operations':
                rows = c.execute('SELECT * FROM asset_operations WHERE account_id=? ORDER BY created_at DESC,id LIMIT ?', (filters.get('account_id'), limit))
                results = [dict(r) for r in rows]
                for result in results:
                    result['entries'] = [dict(r) for r in c.execute('SELECT e.*,d.detail FROM asset_entries e LEFT JOIN asset_entry_details d '
                                                                   'ON e.operation_id=d.operation_id AND e.entry_no=d.entry_no WHERE e.operation_id=? ORDER BY e.entry_no', (result['id'],))]
                return results
            if view == 'outbox':
                return [dict(r) for r in c.execute('SELECT id,type,account_id,status,attempts,last_error FROM outbox ORDER BY id DESC LIMIT ?', (limit,))]
            if view == 'dashboard':
                return {'accounts': c.execute('SELECT COUNT(*) FROM accounts').fetchone()[0],
                        'active_sessions': c.execute('SELECT COUNT(*) FROM sessions WHERE revoked=0 AND expires_at>?', (self.clock(),)).fetchone()[0],
                        'rooms': c.execute("SELECT COUNT(*) FROM rooms WHERE state!='CLOSED'").fetchone()[0],
                        'pending_sync': c.execute("SELECT COUNT(*) FROM outbox WHERE status!='DELIVERED'").fetchone()[0],
                        'profile': self.catalog['id'], 'retail_compatible': False}
            raise Fault('NOT_FOUND', 'Unknown view', 404)

    def _user_view(self, c, account_id):
        row = self._account(c, account_id)
        result = {key: row[key] for key in ('id', 'login', 'game_id', 'status', 'created_at', 'last_login')}
        result['character'] = dict(character) if (character := c.execute('SELECT * FROM characters WHERE account_id=?', (account_id,)).fetchone()) else None
        result['wallets'] = [dict(r) for r in c.execute('SELECT currency,balance,version FROM wallets WHERE account_id=?', (account_id,))]
        result['inventory'] = [dict(r) for r in c.execute('SELECT * FROM inventory WHERE account_id=?', (account_id,))]
        result['equipment'] = [dict(r) for r in c.execute('SELECT slot,inventory_id FROM equipment WHERE account_id=?', (account_id,))]
        result['sanctions'] = [dict(r) for r in c.execute('SELECT * FROM sanctions WHERE account_id=?', (account_id,))]
        result['room'] = dict(member) if (member := c.execute('SELECT * FROM room_members WHERE account_id=?', (account_id,)).fetchone()) else None
        result['retail_sync'] = 'UNIMPLEMENTED'
        return result


# Fixed salt and digest are deliberately invalid credentials but incur the same KDF work.
DUMMY_PASSWORD_HASH = 'scrypt$32768$8$3$' + '00' * 16 + '$' + '00' * 64
