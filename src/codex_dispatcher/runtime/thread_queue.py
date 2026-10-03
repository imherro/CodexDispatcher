from __future__ import annotations

import threading

from .locks import ThreadLocks


class ThreadQueue:
    """One durable FIFO consumer per Thread; workers sharing a Thread share this queue."""
    def __init__(self, dispatch, emit=lambda event: None):
        self.dispatch = dispatch
        self.emit = emit
        self._allowed = set()
        self._threads = {}
        self._guard = threading.RLock()
        self._wake = threading.Condition(self._guard)
        self._closing = False
        self._processing = set()

    def allow(self, worker_id, thread_id):
        with self._wake:
            self._allowed.add(worker_id)
            if thread_id not in self._threads or not self._threads[thread_id].is_alive():
                consumer = threading.Thread(target=self._consume, args=(thread_id,), daemon=True,
                                            name='dispatch-' + thread_id[:12])
                self._threads[thread_id] = consumer
                consumer.start()
            self._wake.notify_all()

    def pause(self, worker_id):
        with self._wake:
            self._allowed.discard(worker_id)
            self._wake.notify_all()

    def wake(self):
        with self._wake:
            self._wake.notify_all()

    def _consume(self, thread_id):
        while True:
            with self._wake:
                if self._closing:
                    return
                allowed = self._allowed.copy()
            try:
                rows = self.dispatch.db.queued(thread_id)
                row = next((r for r in rows if r['worker_id'] in allowed), None)
                if not row or self.dispatch.db.thread_blocked(thread_id):
                    with self._wake:
                        self._wake.wait(timeout=5)
                    continue
                with ThreadLocks.get(thread_id):
                    with self._guard:
                        if self._closing or row['worker_id'] not in self._allowed:
                            continue
                        self._processing.add(thread_id)
                    try:
                        result = self.dispatch.process_record(row['id'], can_send=lambda: self._can_send(row['worker_id']))
                    finally:
                        with self._guard:
                            self._processing.discard(thread_id)
                if result in ('busy', 'paused', 'blocked'):
                    attempts = self.dispatch.db.record(row['id'])['attempts']
                    with self._wake:
                        self._wake.wait(timeout=min(5 * 2 ** min(attempts, 5), 160))
            except Exception as exc:
                self.emit({'kind': 'error', 'text': '队列已暂停：' + str(exc)})
                with self._wake:
                    self._allowed.difference_update(allowed)
                    self._wake.wait(timeout=5)

    def close(self):
        with self._wake:
            self._closing = True
            self._allowed.clear()
            self._wake.notify_all()
        # A running turn is allowed to finish. GUI prevents exit while active.

    def running(self):
        with self._guard:
            return list(self._processing | set(self.dispatch.codex.active_threads()))

    def _can_send(self, worker_id):
        with self._guard:
            return not self._closing and worker_id in self._allowed
