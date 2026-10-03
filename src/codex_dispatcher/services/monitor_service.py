from __future__ import annotations

import threading
import time

from codex_dispatcher.domain.models import now
from .security import redact


class MonitorService:
    def __init__(self, dispatch, queue, emit=lambda event: None):
        self.dispatch, self.queue, self.emit = dispatch, queue, emit
        self._workers = {}
        self._checks = {}
        self._guard = threading.RLock()
        self._closed = False

    def start(self, worker):
        if not worker.enabled:
            raise ValueError('Worker 已禁用')
        if not 1 <= worker.poll_interval <= 60:
            raise ValueError('检查间隔必须为 1–60 分钟')
        with self._guard:
            if worker.id in self._workers:
                return
            stop = threading.Event()
            self._workers[worker.id] = stop
            self.queue.allow(worker.id, worker.target_thread_id)
            threading.Thread(target=self._loop, args=(worker, stop), daemon=True,
                             name='monitor-' + worker.name).start()
        self.emit({'kind': 'monitoring', 'worker_id': worker.id, 'text': 'Monitoring'})

    def _loop(self, worker, stop):
        failures = 0
        while not stop.is_set():
            try:
                self.check_now(worker, stop=stop)
                failures = 0
                delay = worker.poll_interval * 60
            except Exception as exc:
                failures += 1
                self.emit({'kind': 'error', 'worker_id': worker.id, 'text': redact(str(exc))})
                if failures >= 5:
                    self.stop(worker.id)
                    self.dispatch.db.set_runtime(worker.id, {'status': 'Error', 'last_result': redact(str(exc))})
                    break
                delay = min(worker.poll_interval * 60 * 2 ** (failures - 1), 3600)
            stop.wait(delay)

    def check_now(self, worker, *, stop=None):
        with self._guard:
            check = self._checks.setdefault(worker.id, threading.Lock())
        if not check.acquire(blocking=False):
            return []
        try:
            if self._closed or (stop and stop.is_set()):
                return []
            # Authorization to execute this manual check / monitor remains in queue until paused.
            identifiers = self.dispatch.discover(worker)
            if not self._closed and not (stop and stop.is_set()):
                self.queue.allow(worker.id, worker.target_thread_id)
            state = {'status': 'Monitoring' if self.is_monitoring(worker.id) else 'Paused',
                     'last_check': now(), 'last_result': f'{len(identifiers)} 个新待办' if identifiers else self.dispatch.db.runtime().get(worker.id, {}).get('last_result', '—')}
            self.dispatch.db.set_runtime(worker.id, state)
            return identifiers
        finally:
            check.release()

    def is_monitoring(self, worker_id):
        with self._guard:
            return worker_id in self._workers

    def stop(self, worker_id):
        with self._guard:
            stop = self._workers.pop(worker_id, None)
        if stop:
            stop.set()
        self.queue.pause(worker_id)
        if stop:
            self.emit({'kind': 'paused', 'worker_id': worker_id, 'text': '已停止监测；已通知的 agent 继续执行'})

    def close(self):
        self._closed = True
        with self._guard:
            identifiers = list(self._workers)
        for identifier in identifiers:
            self.stop(identifier)
        self.queue.close()
