from dataclasses import replace
from types import SimpleNamespace
import json
import threading
import time

import pytest

from codex_dispatcher.domain.models import DispatchError, RecoveryRequired, ThreadBusy, normalize_repository
from codex_dispatcher.services.codex_service import CodexService, RunResult
from codex_dispatcher.services.github_service import GitHubService
from codex_dispatcher.services.monitor_service import MonitorService
from codex_dispatcher.services.notification_service import build_notification
from codex_dispatcher.services.security import redact
from codex_dispatcher.runtime.thread_queue import ThreadQueue
from codex_dispatcher.storage.database import Database


@pytest.mark.parametrize('source', ['owner/repo', 'https://github.com/owner/repo/', 'https://github.com/owner/repo.git'])
def test_repository_normalization(source):
    assert normalize_repository(source) == 'owner/repo'


@pytest.mark.parametrize('source', ['https://evil.test/owner/repo', 'owner/repo/issues', 'owner/../repo', '--help'])
def test_reject_bad_repository(source):
    with pytest.raises(ValueError):
        normalize_repository(source)


def test_empty_poll_has_no_codex_calls_or_target_reads(core, worker, monkeypatch):
    core.github.list_assigned_issues.return_value = []
    monkeypatch.setattr('pathlib.Path.is_dir', lambda _: pytest.fail('target read during empty poll'))
    assert core.service.discover(worker) == []
    assert core.codex.mock_calls == []
    core.github.get_issue.assert_not_called()


@pytest.mark.parametrize('mode,value,labels,assignees,expected', [
    ('label', 'agent:worker-1', ['agent:worker-1'], [], True),
    ('label', 'agent:other', ['agent:worker-1'], [], False),
    ('assignee', 'actual-user', [], ['actual-user'], True),
    ('assignee', 'worker-1', [], ['actual-user'], False),
])
def test_assignment_rule_separate_from_worker_name(worker, issue, mode, value, labels, assignees, expected):
    worker.assignment_mode, worker.assignment_value = mode, value
    issue.labels, issue.assignees = labels, assignees
    assert issue.matches(worker) is expected


def test_closed_and_ignored_issues(worker, issue):
    issue.state = 'CLOSED'
    assert not issue.matches(worker)
    issue.state = 'OPEN'
    issue.labels.append('agent:running')
    assert not issue.matches(worker)


def test_success_deduplicated_and_target_preserved(core, worker):
    ids = core.service.discover(worker)
    assert core.service.process_record(ids[0]) == 'notified'
    assert core.service.discover(worker) == []
    record = core.db.record(ids[0])
    assert record['target_thread_id'] == worker.target_thread_id
    assert record['turn_id'] == 'turn-123'
    assert record['dispatch_time']
    core.codex.send_task.assert_called_once()
    assert core.codex.send_task.call_args.args[0] == worker.target_thread_id
    core.codex.normalize.assert_not_called()


def test_manual_redispatch(core, worker):
    first = core.service.discover(worker)[0]
    core.service.process_record(first)
    second = core.service.redispatch(first)
    assert first != second
    assert core.service.process_record(second) == 'notified'
    assert core.codex.send_task.call_count == 2


def test_update_redispatch_opt_in(core, worker, issue):
    first = core.service.discover(worker)[0]
    core.service.process_record(first)
    issue.updated_at = '2026-10-03T12:00:00Z'
    assert core.service.discover(worker) == []
    assert core.service.discover(worker) == []


def test_failed_submission_not_retried_by_poll(core, worker):
    core.codex.send_task.side_effect = DispatchError('model unavailable')
    record = core.service.discover(worker)[0]
    assert core.service.process_record(record) == 'failed'
    assert core.service.discover(worker) == []


def test_project_thread_mismatch(worker, tmp_path):
    with pytest.raises(DispatchError, match='不属于'):
        CodexService._check_project({'cwd': str(tmp_path / 'another')}, worker.target_project, False)
    CodexService._check_project({'cwd': str(tmp_path / 'another')}, worker.target_project, True)


def test_busy_queued_then_retried(core, worker):
    send = core.codex.send_task.side_effect
    core.codex.send_task.side_effect = ThreadBusy('busy')
    record = core.service.discover(worker)[0]
    assert core.service.process_record(record) == 'busy'
    assert core.db.record(record)['status'] == 'queued'
    core.codex.send_task.side_effect = send
    assert core.service.process_record(record) == 'notified'


def test_busy_retry_is_bounded(core, worker):
    core.codex.send_task.side_effect = ThreadBusy('busy')
    record = core.service.discover(worker)[0]
    for _ in range(6):
        core.service.process_record(record)
    assert core.db.record(record)['status'] == 'failed'
    assert core.db.record(record)['attempts'] == 6


def test_crash_recovery_and_no_automatic_replay(core, worker):
    record = core.service.discover(worker)[0]
    assert core.db.claim(record)
    core.db.update_record(record, status='dispatched', turn_id='known-turn')
    db = Database(core.db.path)
    assert db.recover_startup() == 1
    assert db.record(record)['status'] == 'recovery_required'
    assert not db.eligible(worker, core.github.get_issue.return_value)
    assert db.thread_blocked(worker.target_thread_id)
    assert db.workers()[0].target_thread_id == worker.target_thread_id


def test_uncertain_submission_blocks_shared_thread(core, worker, issue):
    core.codex.send_task.side_effect = RecoveryRequired('ack lost')
    record = core.service.discover(worker)[0]
    assert core.service.process_record(record) == 'recovery_required'
    worker2 = replace(worker, id='worker-two')
    core.db.save_worker(worker2)
    second = core.db.reserve(worker2, issue)
    assert not core.db.claim(second)
    assert core.service.discover(worker) == []


def test_recovery_positive_completed_only(core, worker):
    record = core.service.discover(worker)[0]
    core.db.update_record(record, status='recovery_required', turn_id='turn-123')
    core.codex.inspect_turn.return_value = {'status': 'interrupted', 'items': []}
    core.service.check_recovery(record)
    assert core.db.record(record)['status'] == 'recovery_required'
    core.codex.inspect_turn.return_value = {'status': 'completed', 'items': [{'type': 'agentMessage', 'text': 'done'}]}
    core.service.check_recovery(record)
    assert core.db.record(record)['status'] == 'notified'


def test_recheck_assignment_before_send(core, worker, issue):
    record = core.service.discover(worker)[0]
    issue.labels = []
    assert core.service.process_record(record) == 'ignored'
    core.codex.send_task.assert_not_called()


def test_github_auth_failure():
    runner = lambda *a, **kw: SimpleNamespace(returncode=1, stdout='', stderr='not logged in')
    with pytest.raises(DispatchError, match='not logged in'):
        GitHubService('gh', runner).check_auth()


def test_github_query_uses_argument_array(worker, issue):
    calls = []
    def runner(args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(returncode=0, stdout='[]', stderr='')
    worker.assignment_value = 'label; echo stolen'
    assert GitHubService('gh', runner).list_assigned_issues(worker) == []
    assert calls[0][0][calls[0][0].index('--label') + 1] == worker.assignment_value
    assert not calls[0][1].get('shell')


def test_connection_failure(core, worker):
    core.codex.check_connection.side_effect = DispatchError('not connected')
    with pytest.raises(DispatchError):
        core.service.validate_worker(worker)


def test_monitor_stop_leaves_current_turn_alone(core, worker):
    queue = SimpleNamespace(allow=lambda *a: None, pause=lambda *a: None, close=lambda: None)
    monitor = MonitorService(core.service, queue)
    monitor.start(worker)
    monitor.stop(worker.id)
    assert not monitor.is_monitoring(worker.id)
    core.codex.interrupt.assert_not_called()
    monitor.close()


def test_two_workers_same_thread_serial(core, worker, issue):
    worker2 = replace(worker, id='worker-two', name='second')
    core.db.save_worker(worker2)
    first = core.db.reserve(worker, issue)
    second = core.db.reserve(worker2, issue)
    start, release, finished = threading.Event(), threading.Event(), threading.Event()
    calls = []
    def send(thread_id, prompt, project, **kwargs):
        calls.append(thread_id)
        kwargs['on_started']('turn-' + str(len(calls)))
        if len(calls) == 1:
            start.set()
            assert release.wait(5)
        else:
            finished.set()
        return RunResult('turn', 'completed', 'done')
    core.codex.send_task.side_effect = send
    queue = ThreadQueue(core.service)
    queue.allow(worker.id, worker.target_thread_id)
    queue.allow(worker2.id, worker2.target_thread_id)
    assert start.wait(3)
    time.sleep(.1)
    assert len(calls) == 1
    release.set()
    assert finished.wait(3)
    queue.close()
    assert calls == [worker.target_thread_id] * 2


def test_atomic_claim_two_instances(core, worker, issue):
    worker2 = replace(worker, id='worker-two')
    core.db.save_worker(worker2)
    first = core.db.reserve(worker, issue)
    second = core.db.reserve(worker2, issue)
    assert core.db.claim(first)
    assert not Database(core.db.path).claim(second)


def test_sensitive_history_redacted(core, worker):
    record = core.service.discover(worker)[0]
    core.db.update_record(record, prompt='Bearer TOPSECRET', error='token=PRIVATE', final_response='ghp_ABCDE12345')
    saved = core.db.record(record)
    assert all('[REDACTED]' in saved[k] for k in ('prompt','error','final_response'))


def test_reasoning_event_not_displayed():
    assert CodexService.visible_event('item/reasoning/textDelta', {'delta': 'private'}) is None
    assert CodexService.visible_event('item/completed', {'item': {'type': 'reasoning', 'text': 'private'}}) is None


def test_sdk_default_approval_not_accepted():
    assert CodexService._approval_handler('item/commandExecution/requestApproval', {}) == {'decision':'decline'}
    assert CodexService._approval_handler('item/fileChange/requestApproval', {}) == {'decision':'decline'}


def test_stop_keeps_reserved_task_queued(core, worker):
    identifier = core.service.discover(worker)[0]
    assert core.service.process_record(identifier, can_send=lambda: False) == 'paused'
    assert core.db.record(identifier)['status'] == 'queued'
    core.codex.send_task.assert_not_called()
