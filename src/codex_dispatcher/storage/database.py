from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import sqlite3
import uuid

from codex_dispatcher.domain.models import DispatchError, Issue, Worker, now
from codex_dispatcher.services.security import redact


def data_directory() -> Path:
    return Path(os.environ.get('APPDATA', str(Path.home() / '.local' / 'share'))) / 'CodexDispatcher'


class Database:
    def __init__(self, path: str | Path | None = None):
        self.path = Path(path or data_directory() / 'codex-dispatcher.db')
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            if db.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                raise DispatchError('SQLite 检查失败。请先备份数据库，再恢复备份；应用不会自动删除数据。')
            db.execute('PRAGMA journal_mode=WAL')
            if db.execute('PRAGMA user_version').fetchone()[0] > 2:
                raise DispatchError('数据库版本高于当前程序，请使用较新版 Codex Dispatcher')
            db.executescript('''
                CREATE TABLE IF NOT EXISTS workers (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, config TEXT NOT NULL,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS dispatch_records (
                    id TEXT PRIMARY KEY, worker_id TEXT NOT NULL,
                    repository TEXT NOT NULL, issue_number INTEGER NOT NULL,
                    issue_updated_at TEXT NOT NULL, target_thread_id TEXT NOT NULL,
                    status TEXT NOT NULL, discovered_at TEXT NOT NULL,
                    started_at TEXT, dispatch_time TEXT, finished_at TEXT,
                    turn_id TEXT, prompt TEXT NOT NULL DEFAULT '',
                    error TEXT NOT NULL DEFAULT '', final_response TEXT NOT NULL DEFAULT '',
                    worker_snapshot TEXT NOT NULL, issue_snapshot TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    FOREIGN KEY (worker_id) REFERENCES workers(id)
                );
                CREATE INDEX IF NOT EXISTS records_issue ON dispatch_records(worker_id, repository, issue_number);
                CREATE INDEX IF NOT EXISTS records_queue ON dispatch_records(target_thread_id, status, discovered_at);
                CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS runtime_state (worker_id TEXT PRIMARY KEY, value TEXT NOT NULL);
            ''')
            # Migrate in place; older records retain their per-Issue deduplication.
            columns = {row['name'] for row in db.execute('PRAGMA table_info(dispatch_records)')}
            if 'notification_key' not in columns:
                db.execute("ALTER TABLE dispatch_records ADD COLUMN notification_key TEXT NOT NULL DEFAULT 'issue'")
            db.execute('CREATE INDEX IF NOT EXISTS records_notification ON dispatch_records(worker_id, repository, issue_number, notification_key)')
            db.execute('PRAGMA user_version=2')

    @contextmanager
    def connection(self):
        try:
            db = sqlite3.connect(str(self.path), timeout=5)
            db.row_factory = sqlite3.Row
            db.execute('PRAGMA foreign_keys=ON')
            try:
                with db:
                    yield db
            finally:
                db.close()
        except sqlite3.Error as exc:
            raise DispatchError(redact('SQLite 错误：' + str(exc))) from exc

    def save_worker(self, worker: Worker):
        with self.connection() as db:
            db.execute('''INSERT INTO workers VALUES (?, ?, ?, ?, ?)
                          ON CONFLICT(id) DO UPDATE SET name=excluded.name,
                          config=excluded.config, updated_at=excluded.updated_at''',
                       (worker.id, worker.name, json.dumps(worker.to_dict(), ensure_ascii=False),
                        worker.created_at, worker.updated_at))

    def workers(self):
        with self.connection() as db:
            return [Worker.from_dict(json.loads(r['config'])) for r in db.execute('SELECT config FROM workers ORDER BY created_at')]

    def get_worker(self, worker_id):
        with self.connection() as db:
            row = db.execute('SELECT config FROM workers WHERE id=?', (worker_id,)).fetchone()
            return Worker.from_dict(json.loads(row['config'])) if row else None

    def eligible(self, worker: Worker, issue: Issue):
        with self.connection() as db:
            return self._eligible(db, worker, issue)

    @staticmethod
    def _eligible(db, worker, issue):
        rows = db.execute('''SELECT 1 FROM dispatch_records
                             WHERE worker_id=? AND repository=? COLLATE NOCASE AND issue_number=? AND notification_key=?''',
                          (worker.id, issue.repository, issue.number, issue.notification_key)).fetchall()
        if not rows:
            return True
        # Failure and ignored rows also require explicit retry; a poll never loops on failure.
        return False

    def reserve(self, worker: Worker, issue: Issue, *, force=False):
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            if not force and not self._eligible(db, worker, issue):
                return None
            if force and db.execute('''SELECT 1 FROM dispatch_records WHERE worker_id=?
                    AND repository=? COLLATE NOCASE AND issue_number=? AND notification_key=?
                    AND (status IN ('discovered','queued','dispatching','dispatched','recovery_required')
                         OR (status='notified' AND finished_at IS NULL))''',
                    (worker.id, issue.repository, issue.number, issue.notification_key)).fetchone():
                raise DispatchError('任务仍在排队、运行或等待恢复检查。请先检查或标记已处理，再重新派送。')
            identifier = str(uuid.uuid4())
            db.execute('''INSERT INTO dispatch_records
                (id,worker_id,repository,issue_number,issue_updated_at,target_thread_id,status,
                 discovered_at,worker_snapshot,issue_snapshot,notification_key)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)''',
                (identifier, worker.id, issue.repository, issue.number, issue.updated_at,
                 worker.target_thread_id, 'queued', now(),
                 json.dumps(worker.to_dict(), ensure_ascii=False),
                 json.dumps(issue.metadata(), ensure_ascii=False), issue.notification_key))
            return identifier

    def update_record(self, identifier, **changes):
        allowed = {'status', 'started_at', 'dispatch_time', 'finished_at', 'turn_id', 'prompt',
                   'error', 'final_response', 'attempts', 'issue_updated_at', 'issue_snapshot'}
        if not changes.keys() <= allowed:
            raise ValueError('未知派送记录字段')
        values = [redact(v) if isinstance(v, str) and k in ('prompt', 'error', 'final_response', 'issue_snapshot') else v
                  for k, v in changes.items()]
        with self.connection() as db:
            db.execute('UPDATE dispatch_records SET ' + ','.join(k + '=?' for k in changes) + ' WHERE id=?',
                       (*values, identifier))

    def record(self, identifier):
        with self.connection() as db:
            row = db.execute('SELECT * FROM dispatch_records WHERE id=?', (identifier,)).fetchone()
            return dict(row) if row else None

    def history(self, limit=1000):
        with self.connection() as db:
            return [dict(r) for r in db.execute('''SELECT r.*, w.name AS worker_name FROM dispatch_records r
                    JOIN workers w ON w.id=r.worker_id ORDER BY r.discovered_at DESC, r.rowid DESC LIMIT ?''', (limit,))]

    def queued(self, thread_id):
        with self.connection() as db:
            return [dict(r) for r in db.execute('''SELECT * FROM dispatch_records WHERE target_thread_id=?
                    AND status='queued' ORDER BY discovered_at, rowid''', (thread_id,))]

    def claim(self, identifier):
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            record = db.execute('SELECT * FROM dispatch_records WHERE id=?', (identifier,)).fetchone()
            if not record or record['status'] != 'queued':
                return False
            if db.execute('''SELECT 1 FROM dispatch_records WHERE target_thread_id=? AND id<>?
                    AND (status IN ('dispatching','dispatched','recovery_required') OR (status='notified' AND finished_at IS NULL))''',
                    (record['target_thread_id'], identifier)).fetchone():
                return False
            db.execute("UPDATE dispatch_records SET status='dispatching', started_at=? WHERE id=?", (now(), identifier))
            return True

    def thread_blocked(self, thread_id):
        with self.connection() as db:
            return bool(db.execute('''SELECT 1 FROM dispatch_records WHERE target_thread_id=?
                    AND (status IN ('recovery_required','dispatching','dispatched') OR (status='notified' AND finished_at IS NULL)) LIMIT 1''', (thread_id,)).fetchone())

    def recover_startup(self):
        with self.connection() as db:
            # An acknowledged notification is never replayed after restart.
            db.execute("UPDATE dispatch_records SET finished_at=? WHERE status='notified' AND finished_at IS NULL", (now(),))
            return db.execute('''UPDATE dispatch_records SET status='recovery_required',
                error='需要恢复检查：上次进程退出时任务可能已提交，禁止自动重复发送。'
                WHERE status IN ('dispatching','dispatched')''').rowcount

    def set_runtime(self, worker_id, value):
        with self.connection() as db:
            db.execute('INSERT INTO runtime_state VALUES (?,?) ON CONFLICT(worker_id) DO UPDATE SET value=excluded.value',
                       (worker_id, json.dumps(value, ensure_ascii=False)))

    def runtime(self):
        with self.connection() as db:
            return {r['worker_id']: json.loads(r['value']) for r in db.execute('SELECT * FROM runtime_state')}

    def setting(self, key, default=None):
        with self.connection() as db:
            row = db.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()
            return json.loads(row['value']) if row else default

    def set_setting(self, key, value):
        with self.connection() as db:
            db.execute('INSERT INTO settings VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                       (key, json.dumps(value, ensure_ascii=False)))
