PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS schema_version(version INTEGER PRIMARY KEY CHECK(version=1));
INSERT OR IGNORE INTO schema_version VALUES(1);
CREATE TABLE IF NOT EXISTS deployment_metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS admins(
 id TEXT PRIMARY KEY, login TEXT NOT NULL UNIQUE, password_hash TEXT NOT NULL,
 otp_secret TEXT NOT NULL, last_otp_step INTEGER NOT NULL DEFAULT -1,
 role TEXT NOT NULL CHECK(role IN ('owner','users','resources','auditor')),
 active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)), security_version INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS accounts(
 id TEXT PRIMARY KEY, game_id INTEGER NOT NULL UNIQUE,
 login TEXT NOT NULL UNIQUE, password_hash TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('PENDING_REVIEW','ACTIVE','DISABLED')),
 security_version INTEGER NOT NULL DEFAULT 1, created_at INTEGER NOT NULL, last_login INTEGER
);
CREATE TABLE IF NOT EXISTS invitations(
 id TEXT PRIMARY KEY, token_hash TEXT NOT NULL UNIQUE,
 max_uses INTEGER NOT NULL CHECK(max_uses>0), uses INTEGER NOT NULL DEFAULT 0 CHECK(uses>=0 AND uses<=max_uses),
 expires_at INTEGER NOT NULL, revoked INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS characters(
 id TEXT PRIMARY KEY, account_id TEXT NOT NULL UNIQUE REFERENCES accounts(id),
 nickname TEXT NOT NULL UNIQUE, init_state TEXT NOT NULL CHECK(init_state='READY'), profile TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions(
 token_hash TEXT PRIMARY KEY, subject_id TEXT NOT NULL, kind TEXT NOT NULL CHECK(kind IN ('player','admin')),
 security_version INTEGER NOT NULL, expires_at INTEGER NOT NULL, revoked INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS sanctions(
 id TEXT PRIMARY KEY, account_id TEXT NOT NULL REFERENCES accounts(id), reason TEXT NOT NULL,
 starts_at INTEGER NOT NULL, expires_at INTEGER, revoked INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS audit_events(
 id INTEGER PRIMARY KEY AUTOINCREMENT, actor TEXT NOT NULL, action TEXT NOT NULL, target TEXT,
 request_id TEXT NOT NULL, time INTEGER NOT NULL, result TEXT NOT NULL, reason TEXT NOT NULL, detail TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS requests(
 scope TEXT NOT NULL, key TEXT NOT NULL, payload_hash TEXT NOT NULL, result TEXT NOT NULL,
 PRIMARY KEY(scope,key)
);
CREATE TABLE IF NOT EXISTS wallets(
 account_id TEXT NOT NULL REFERENCES accounts(id), currency TEXT NOT NULL, balance INTEGER NOT NULL CHECK(balance>=0),
 version INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(account_id,currency)
);
CREATE TABLE IF NOT EXISTS inventory(
 id TEXT PRIMARY KEY, account_id TEXT NOT NULL REFERENCES accounts(id), item_key TEXT NOT NULL,
 quantity INTEGER NOT NULL CHECK(quantity>0), expires_at INTEGER, bound INTEGER NOT NULL DEFAULT 1,
 version INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS equipment(
 account_id TEXT NOT NULL REFERENCES accounts(id), slot TEXT NOT NULL, inventory_id TEXT NOT NULL REFERENCES inventory(id),
 PRIMARY KEY(account_id,slot)
);
CREATE TABLE IF NOT EXISTS asset_operations(
 id TEXT PRIMARY KEY, account_id TEXT NOT NULL REFERENCES accounts(id), actor TEXT NOT NULL,
 source TEXT NOT NULL, reason TEXT NOT NULL, created_at INTEGER NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('COMMITTED','MANUAL_REVIEW')), linked_operation TEXT REFERENCES asset_operations(id)
);
CREATE TABLE IF NOT EXISTS asset_entries(
 operation_id TEXT NOT NULL REFERENCES asset_operations(id), entry_no INTEGER NOT NULL,
 kind TEXT NOT NULL, resource TEXT NOT NULL, instance_id TEXT, delta INTEGER NOT NULL,
 before_value INTEGER NOT NULL, after_value INTEGER NOT NULL, PRIMARY KEY(operation_id,entry_no)
);
CREATE TABLE IF NOT EXISTS outbox(
 id INTEGER PRIMARY KEY AUTOINCREMENT, event_key TEXT NOT NULL UNIQUE, type TEXT NOT NULL, account_id TEXT,
 payload TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'PENDING', attempts INTEGER NOT NULL DEFAULT 0,
 next_retry INTEGER NOT NULL DEFAULT 0, delivered_at INTEGER, last_error TEXT
);
CREATE TABLE IF NOT EXISTS asset_entry_details(
 operation_id TEXT NOT NULL, entry_no INTEGER NOT NULL, detail TEXT NOT NULL,
 PRIMARY KEY(operation_id,entry_no), FOREIGN KEY(operation_id,entry_no) REFERENCES asset_entries(operation_id,entry_no)
);
CREATE TABLE IF NOT EXISTS rooms(
 id TEXT PRIMARY KEY, owner TEXT NOT NULL REFERENCES accounts(id), map_key TEXT NOT NULL,
 capacity INTEGER NOT NULL CHECK(capacity>0), state TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 1,
 match_id TEXT
);
CREATE TABLE IF NOT EXISTS room_members(
 account_id TEXT PRIMARY KEY REFERENCES accounts(id), room_id TEXT NOT NULL REFERENCES rooms(id),
 slot INTEGER NOT NULL, ready INTEGER NOT NULL DEFAULT 0, joined_at INTEGER NOT NULL,
 UNIQUE(room_id,slot)
);
CREATE TABLE IF NOT EXISTS matches(
 id TEXT PRIMARY KEY, room_id TEXT NOT NULL REFERENCES rooms(id), instance_id TEXT NOT NULL UNIQUE,
 epoch TEXT NOT NULL, state TEXT NOT NULL, snapshot TEXT NOT NULL,
 node_token_hash TEXT NOT NULL, created_at INTEGER NOT NULL, lease_until INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS match_players(
 match_id TEXT NOT NULL REFERENCES matches(id), account_id TEXT NOT NULL REFERENCES accounts(id),
 security_version INTEGER NOT NULL, eligible INTEGER NOT NULL DEFAULT 1, PRIMARY KEY(match_id,account_id)
);
CREATE TABLE IF NOT EXISTS settlements(
 match_id TEXT PRIMARY KEY REFERENCES matches(id), payload_hash TEXT NOT NULL, result TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS immutable_audit_update BEFORE UPDATE ON audit_events BEGIN SELECT RAISE(ABORT,'immutable audit'); END;
CREATE TRIGGER IF NOT EXISTS immutable_audit_delete BEFORE DELETE ON audit_events BEGIN SELECT RAISE(ABORT,'immutable audit'); END;
CREATE TRIGGER IF NOT EXISTS immutable_entries_update BEFORE UPDATE ON asset_entries BEGIN SELECT RAISE(ABORT,'immutable ledger'); END;
CREATE TRIGGER IF NOT EXISTS immutable_entries_delete BEFORE DELETE ON asset_entries BEGIN SELECT RAISE(ABORT,'immutable ledger'); END;
CREATE TRIGGER IF NOT EXISTS immutable_details_update BEFORE UPDATE ON asset_entry_details BEGIN SELECT RAISE(ABORT,'immutable ledger details'); END;
CREATE TRIGGER IF NOT EXISTS immutable_details_delete BEFORE DELETE ON asset_entry_details BEGIN SELECT RAISE(ABORT,'immutable ledger details'); END;
