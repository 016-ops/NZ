from contextlib import contextmanager
from pathlib import Path
import sqlite3


class ClosingConnection(sqlite3.Connection):
    def __exit__(self, *args):
        try:
            return super().__exit__(*args)
        finally:
            self.close()


class Database:
    def __init__(self, path):
        self.path = str(Path(path).resolve())
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            connection.execute('PRAGMA journal_mode=WAL')
            connection.executescript(Path(__file__).with_name('schema.sql').read_text(encoding='utf-8'))

    def connect(self):
        connection = sqlite3.connect(self.path, timeout=15, isolation_level=None, factory=ClosingConnection)
        connection.row_factory = sqlite3.Row
        connection.execute('PRAGMA foreign_keys=ON')
        connection.execute('PRAGMA synchronous=FULL')
        return connection

    @contextmanager
    def transaction(self):
        connection = self.connect()
        try:
            connection.execute('BEGIN IMMEDIATE')
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def backup(self, destination):
        path = Path(destination).resolve()
        if str(path) == self.path or path.exists():
            raise ValueError('Backup requires a new destination')
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as source, sqlite3.connect(path, factory=ClosingConnection) as target:
            source.backup(target)
            if target.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise RuntimeError('Backup integrity check failed')
