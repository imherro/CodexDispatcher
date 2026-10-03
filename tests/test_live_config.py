from dataclasses import replace
from threading import Event
from unittest.mock import Mock
import time

from codex_dispatcher.services.monitor_service import MonitorService


def wait_until(predicate):
    deadline = time.monotonic() + 3
    while not predicate() and time.monotonic() < deadline:
        Event().wait(.01)
    assert predicate()


def test_save_updates_sleeping_monitor_and_preserves_queued_snapshot(core, worker):
    identifier = core.service.discover(worker)[0]
    original = core.db.record(identifier)['worker_snapshot']
    core.service.discover = Mock(return_value=[])
    monitor = MonitorService(core.service, Mock())
    try:
        monitor.start(worker)
        wait_until(lambda: 0 < (monitor.countdown(worker.id) or 0) <= 300)
        updated = replace(worker, poll_interval=1, target_thread_id='new-thread',
                          notification_template='{notification_id} 新格式 {issue_url}')
        core.db.save_worker(updated)
        monitor.update_worker(updated)
        assert monitor.is_monitoring(worker.id)
        assert 0 < monitor.countdown(worker.id) <= 60
        assert core.service.discover.call_count == 1
        # Advance this monitor's deadline to exercise a genuine next periodic scan.
        with monitor._wake:
            monitor._next_checks[worker.id] = time.monotonic()
            monitor._wake.notify_all()
        wait_until(lambda: core.service.discover.call_count >= 2)
        assert core.service.discover.call_args.args[0] == updated
        assert core.db.record(identifier)['worker_snapshot'] == original
        core.github.get_issue.return_value.assignees = []
        assert core.service.process_record(identifier) == 'notified'
        assert core.codex.send_task.call_args.args[0] == worker.target_thread_id
        assert '新格式' not in core.codex.send_task.call_args.args[1]
        monitor.update_worker(replace(updated, enabled=False))
        assert not monitor.is_monitoring(worker.id)
        monitor.queue.pause.assert_called_with(worker.id)
    finally:
        monitor.close()


def test_save_during_scan_keeps_scan_snapshot_but_next_check_uses_new_config(core, worker):
    entered, release = Event(), Event()
    seen = []
    def discover(config):
        seen.append(config)
        if len(seen) == 1:
            entered.set()
            assert release.wait(3)
        return []
    core.service.discover = discover
    monitor = MonitorService(core.service, Mock())
    try:
        monitor.start(worker)
        assert entered.wait(3)
        updated = replace(worker, assignment_value='new-label', poll_interval=2)
        core.db.save_worker(updated)
        monitor.update_worker(updated)
        assert seen == [worker]
        release.set()
        monitor.check_now(worker, wait=True)
        assert seen == [worker, updated]
        assert monitor.is_monitoring(worker.id)
        core.codex.send_task.assert_not_called()
    finally:
        release.set()
        monitor.close()


def test_resume_after_changing_target_keeps_old_queued_notifications_deliverable(core, worker):
    from codex_dispatcher.runtime.thread_queue import ThreadQueue
    identifier = core.service.discover(worker)[0]
    updated = replace(worker, target_thread_id='new-thread')
    core.db.save_worker(updated)
    queue = ThreadQueue(core.service)
    try:
        queue.allow(updated.id, updated.target_thread_id)
        wait_until(lambda: core.db.record(identifier)['finished_at'] is not None)
        assert core.db.record(identifier)['status'] == 'notified'
        assert core.codex.send_task.call_args.args[0] == worker.target_thread_id
        assert updated.target_thread_id in queue._threads
    finally:
        queue.close()
