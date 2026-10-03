from concurrent.futures import ThreadPoolExecutor
from threading import Event
from unittest.mock import Mock

from codex_dispatcher.services.monitor_service import MonitorService


def test_manual_click_waits_for_running_scan_then_checks_fresh_replies(core, worker):
    entered, release = Event(), Event()
    calls = []
    def discover(_):
        calls.append(1)
        if len(calls) == 1:
            entered.set()
            assert release.wait(3)
            return []
        return ['new-comment-notice']
    core.service.discover = discover
    monitor = MonitorService(core.service, Mock())
    with ThreadPoolExecutor(2) as pool:
        periodic = pool.submit(monitor.check_now, worker)
        assert entered.wait(3)
        assert monitor.countdown(worker.id) == 0
        manual = pool.submit(monitor.check_now, worker, wait=True)
        try:
            assert not manual.done()
        finally:
            release.set()
        assert periodic.result(3) == []
        assert manual.result(3) == ['new-comment-notice']
    assert len(calls) == 2
    assert monitor.countdown(worker.id) is None
    assert core.db.runtime()[worker.id]['last_result'] == '1 个新待办'


def test_manual_check_cancelled_by_stop_does_not_scan_or_reenable_queue(core, worker):
    monitor = MonitorService(core.service, Mock())
    stop = Event()
    monitor._workers[worker.id] = stop
    stop.set()
    core.service.discover = Mock()
    assert monitor.check_now(worker, wait=True) is None
    core.service.discover.assert_not_called()
    monitor.queue.allow.assert_not_called()
