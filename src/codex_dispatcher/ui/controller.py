from concurrent.futures import ThreadPoolExecutor
import logging

from PySide6.QtCore import QObject, Signal

from codex_dispatcher.services.codex_service import CodexService
from codex_dispatcher.services.dispatch_service import DispatchService
from codex_dispatcher.services.github_service import GitHubService
from codex_dispatcher.services.monitor_service import MonitorService
from codex_dispatcher.services.security import redact
from codex_dispatcher.runtime.thread_queue import ThreadQueue
from codex_dispatcher.storage.database import Database


class AppController(QObject):
    finished = Signal(str, object, object)
    event = Signal(dict)

    def __init__(self, data_path=None, github=None, codex=None):
        super().__init__()
        self.pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix='dispatcher-ui')
        self.db = None
        self.data_path, self.github, self.codex = data_path, github or GitHubService(), codex or CodexService()
        self.dispatch = self.queue = self.monitor = None
        self._jobs = set()
        self.closing = False

    def submit(self, name, function):
        if self.closing or name in self._jobs:
            return False
        self._jobs.add(name)
        future = self.pool.submit(function)
        def complete(f):
            try:
                self.finished.emit(name, f.result(), None)
            except Exception as exc:
                self.finished.emit(name, None, redact(str(exc)))
        future.add_done_callback(complete)
        return True

    def acknowledge(self, name):
        self._jobs.discard(name)

    def initialize(self):
        def init():
            self.db = Database(self.data_path)
            recovery = self.db.recover_startup()
            self.dispatch = DispatchService(self.db, self.github, self.codex, self.emit)
            self.queue = ThreadQueue(self.dispatch, self.emit)
            self.monitor = MonitorService(self.dispatch, self.queue, self.emit)
            return {'recovery': recovery, **self.snapshot()}
        return self.submit('initialize', init)

    def emit(self, event):
        event = {**event, 'text': redact(event.get('text', ''))}
        if event['kind'] not in ('message', 'checked'):
            level = logging.ERROR if event['kind'] == 'error' else logging.INFO
            logging.getLogger('codex_dispatcher').log(level, event.get('text', ''))
        self.event.emit(event)

    def snapshot(self):
        workers = self.db.workers()
        runtime = self.db.runtime()
        history = self.db.history()
        for worker in workers:
            state = runtime.setdefault(worker.id, {})
            state['status'] = 'Monitoring' if self.monitor and self.monitor.is_monitoring(worker.id) else 'Paused'
            records = [r for r in history if r['worker_id'] == worker.id]
            state['queue'] = sum(r['status'] == 'queued' for r in records)
            current = next((r for r in records if r['status'] in ('dispatching','dispatched','recovery_required')), None)
            state['current_issue'] = '#' + str(current['issue_number']) if current else '—'
            state['thread_state'] = current['status'] if current else 'Idle / 未验证全局状态'
            if current and current['status'] == 'recovery_required':
                state['status'] = 'Recovery required'
            if records and records[0]['status'] == 'failed':
                state['last_result'] = records[0]['error'] or 'failed'
        return {'workers': workers, 'runtime': runtime, 'history': history}

    def shutdown(self):
        self.closing = True
        if self.monitor:
            self.monitor.close()
        self.pool.shutdown(wait=False, cancel_futures=True)
