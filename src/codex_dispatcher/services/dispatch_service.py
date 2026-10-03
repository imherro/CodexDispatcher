from __future__ import annotations

import json

from codex_dispatcher.domain.models import DispatchError, Issue, RecoveryRequired, ThreadBusy, Worker, WorkerPaused, now
from .notification_service import build_notification
from .security import redact
from .github_service import GitHubUnavailable


class DispatchService:
    def __init__(self, database, github, codex, emit=lambda event: None):
        self.db, self.github, self.codex, self.emit = database, github, codex, emit

    def validate_worker(self, worker):
        self.codex.check_connection()
        thread = self.codex.read_thread(worker.target_thread_id)
        if thread.get("id") != worker.target_thread_id:
            raise DispatchError("目标会话 ID 不一致")
        worker.target_project = thread.get("cwd") or ""
        worker.target_thread_name = thread.get("name") or ""
        worker.validate()
        return worker

    def test_configuration(self, worker):
        self.github.test_repository(worker.repository)
        self.validate_worker(worker)
        return 'GitHub OK · Repository OK · Project OK · Codex OK · Thread OK'

    def discover(self, worker):
        # Do not validate / connect to Codex before finding an eligible candidate.
        # No models, thread metadata, git commands, or target project reads on an empty poll.
        issues = self.github.list_assigned_issues(worker)
        identifiers = []
        for issue in issues:
            if not issue.matches(worker):
                continue
            identifier = self.db.reserve(worker, issue)
            if identifier:
                identifiers.append(identifier)
                self.emit({'kind': 'discovered', 'worker_id': worker.id, 'text': f'发现 Issue #{issue.number}，已排队'})
        return identifiers

    def _read_source(self, worker, number, notification_key):
        if worker.assignment_mode != 'mention':
            return self.github.get_issue(worker.repository, number)
        comment_id = None
        if notification_key.startswith('mention:comment:'):
            comment_id = int(notification_key.rsplit(':', 1)[1])
        return self.github.get_issue(worker.repository, number, mention=True, comment_id=comment_id)

    def process_record(self, identifier, can_send=lambda: True):
        record = self.db.record(identifier)
        if not record or not self.db.claim(identifier):
            return 'blocked'
        worker = Worker.from_dict(json.loads(record['worker_snapshot']))
        try:
            # Re-read assignment immediately before delivery; label/assignee may have changed while queued.
            issue = self._read_source(worker, record['issue_number'], record['notification_key'])
            if not issue.matches(worker):
                self.db.update_record(identifier, status='ignored', finished_at=now(), error='Issue 已关闭、取消分配或有忽略标签')
                return 'ignored'
            if self.db.get_worker(worker.id) and not self.db.get_worker(worker.id).enabled:
                self.db.update_record(identifier, status='queued', error='Worker 已禁用')
                return 'paused'
            worker.validate()
            if not can_send():
                raise WorkerPaused('监视已停止，任务保留在队列中。')
            prompt = build_notification(worker.repository, issue.number, identifier, comment_id=issue.comment_id,
                                        template=worker.notification_template)
            self.db.update_record(identifier, prompt=prompt, issue_updated_at=issue.updated_at,
                                  issue_snapshot=json.dumps(issue.metadata(), ensure_ascii=False))
            def started(turn_id):
                self.db.update_record(identifier, status='notified', turn_id=turn_id, dispatch_time=now(), error='')
                self.emit({'kind': 'notified', 'worker_id': worker.id, 'record_id': identifier,
                           'text': f'Issue #{issue.number} 已通知目标会话'})
            self.codex.send_task(worker.target_thread_id, prompt, worker.target_project,
                                 trusted=True, on_started=started, can_send=can_send)
            if self.db.record(identifier)['status'] != 'notified':
                raise RecoveryRequired('没有收到通知回执，需要检查原会话；禁止自动重发。')
            # Keep the runtime alive until its turn ends, without relaying output or
            # interpreting the agent's work. Notification success is its ACK above.
            self.db.update_record(identifier, finished_at=now())
            return 'notified'
        except WorkerPaused as exc:
            self.db.update_record(identifier, status='queued', error=str(exc))
            return 'paused'
        except (ThreadBusy, GitHubUnavailable) as exc:
            attempts = record['attempts'] + 1
            keep_queued = isinstance(exc, GitHubUnavailable) or exc.keep_queued
            status = 'queued' if keep_queued or attempts < 6 else 'failed'
            self.db.update_record(identifier, status=status, attempts=attempts, error=str(exc),
                                  **({'finished_at': now()} if status == 'failed' else {}))
            if record['error'] != str(exc) or status == 'failed':
                self.emit({'kind': 'waiting', 'worker_id': worker.id, 'text': str(exc)})
            return 'busy' if status == 'queued' else 'failed'
        except RecoveryRequired as exc:
            current = self.db.record(identifier)
            if current['status'] == 'notified':
                self.db.update_record(identifier, error='通知已确认；agent 运行状态无法继续读取，请检查原会话。')
                self.emit({'kind': 'error', 'worker_id': worker.id, 'record_id': identifier,
                           'text': '通知已确认，agent 连接状态不确定，请在通知记录中检查。'})
                return 'notified'
            self.db.update_record(identifier, status='recovery_required', error=str(exc))
            self.emit({'kind': 'error', 'worker_id': worker.id, 'record_id': identifier, 'text': str(exc)})
            return 'recovery_required'
        except Exception as exc:
            # If ack was persisted, failure is uncertain until the turn is inspected.
            current = self.db.record(identifier)
            status = 'notified' if current['status'] == 'notified' else 'recovery_required' if current['status'] == 'dispatched' else 'failed'
            self.db.update_record(identifier, status=status, error=redact(str(exc)),
                                  **({'finished_at': now()} if status in ('failed', 'notified') else {}))
            self.emit({'kind': 'error', 'worker_id': worker.id, 'record_id': identifier, 'text': redact(str(exc))})
            return status

    def redispatch(self, identifier):
        record = self.db.record(identifier)
        if not record:
            raise DispatchError('历史记录不存在')
        worker = self.db.get_worker(record['worker_id'])
        if not worker or not worker.enabled:
            raise DispatchError('请先启用此 Worker')
        issue = self._read_source(worker, record['issue_number'], record['notification_key'])
        if not issue.matches(worker):
            raise DispatchError('Issue 已不符合当前分配规则，不能重新派送')
        return self.db.reserve(worker, issue, force=True)

    def check_recovery(self, identifier):
        record = self.db.record(identifier)
        if record['status'] != 'recovery_required' and not (record['status'] == 'notified' and not record['finished_at']):
            raise DispatchError('此任务无需恢复检查')
        if not record['turn_id']:
            return '发送结果未知，未保存 Turn ID。请查看目标会话中的通知 ID，再标记已处理或手动重新派送。'
        turn = self.codex.inspect_turn(record['target_thread_id'], record['turn_id'])
        if not turn:
            return '暂未在会话中找到该 Turn；仍保持恢复检查，不自动重新发送。'
        # A second process can expose synthetic interrupted history for an actually live turn.
        # Only a persisted completed turn is a positive recovery signal after restart.
        if turn['status'] == 'completed':
            self.db.update_record(identifier, status='notified', finished_at=now(), error='')
            self.codex.release_terminal(record['target_thread_id'])
            return '已确认 Codex Turn completed，历史状态已恢复。'
        return f"Turn 状态：{turn['status']}。请到原会话确认；任务保持恢复检查。"

    def mark_handled(self, identifier):
        record = self.db.record(identifier)
        if record['status'] in ('dispatching', 'dispatched'):
            raise DispatchError('当前任务仍在运行，不能标记已处理')
        if record['target_thread_id'] in self.codex.active_threads():
            turn = self.codex.inspect_turn(record['target_thread_id'], record['turn_id']) if record['turn_id'] else None
            if not turn or turn['status'] == 'inProgress':
                raise DispatchError('本进程仍持有活动任务，请先安全中断或等待结束')
            self.codex.release_terminal(record['target_thread_id'])
        self.db.update_record(identifier, status='ignored', finished_at=now(), error='用户手动标记已处理')
