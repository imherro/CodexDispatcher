from __future__ import annotations

import json

from codex_dispatcher.domain.models import DispatchError, Issue, RecoveryRequired, ThreadBusy, Worker, WorkerPaused, now
from .template_service import build_task, validate_template
from .security import redact


class DispatchService:
    def __init__(self, database, github, codex, emit=lambda event: None):
        self.db, self.github, self.codex, self.emit = database, github, codex, emit

    def validate_worker(self, worker):
        worker.validate()
        validate_template(worker.prompt_template)
        self.codex.check_connection()
        self.codex.validate_thread(worker.target_thread_id, worker.target_project, worker.allow_mismatch)
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

    def dry_run(self, worker, *, use_normalizer=False):
        prompts = []
        for candidate in self.github.list_assigned_issues(worker):
            if not candidate.matches(worker) or not self.db.eligible(worker, candidate):
                continue
            issue = self.github.get_issue(worker.repository, candidate.number)
            if not issue.matches(worker):
                continue
            normalized = self.codex.normalize(issue, worker.dispatcher_model, worker.dispatcher_reasoning) \
                if worker.dispatcher_enabled and use_normalizer else None
            prompts.append({'issue': issue.number, 'project': worker.target_project,
                            'thread_id': worker.target_thread_id, 'prompt': redact(build_task(worker, issue, normalized))})
        return prompts

    def process_record(self, identifier, can_send=lambda: True):
        record = self.db.record(identifier)
        if not record or not self.db.claim(identifier):
            return 'blocked'
        worker = Worker(**json.loads(record['worker_snapshot']))
        try:
            # Re-read assignment immediately before delivery; label/assignee may have changed while queued.
            issue = self.github.get_issue(record['repository'], record['issue_number'])
            if not issue.matches(worker):
                self.db.update_record(identifier, status='ignored', finished_at=now(), error='Issue 已关闭、取消分配或有忽略标签')
                return 'ignored'
            if self.db.get_worker(worker.id) and not self.db.get_worker(worker.id).enabled:
                self.db.update_record(identifier, status='queued', error='Worker 已禁用')
                return 'paused'
            worker.validate()
            if not can_send():
                raise WorkerPaused('监视已停止，任务保留在队列中。')
            normalized = self.codex.normalize(issue, worker.dispatcher_model, worker.dispatcher_reasoning) \
                if worker.dispatcher_enabled else None
            prompt = build_task(worker, issue, normalized, dispatch_id=identifier)
            self.db.update_record(identifier, prompt=prompt, issue_updated_at=issue.updated_at,
                                  issue_snapshot=json.dumps(issue.to_dict(), ensure_ascii=False))
            def started(turn_id):
                self.db.update_record(identifier, status='dispatched', turn_id=turn_id, dispatch_time=now(), error='')
                self.emit({'kind': 'running', 'worker_id': worker.id, 'record_id': identifier,
                           'text': f'Issue #{issue.number} 已派送，Codex Running'})
            def event(data):
                self.emit({**data, 'worker_id': worker.id, 'record_id': identifier})
            result = self.codex.send_task(worker.target_thread_id, prompt, worker.target_project,
                                         allow_mismatch=worker.allow_mismatch,
                                         model=worker.target_model if worker.override_target_model else None,
                                         on_started=started, on_event=event, can_send=can_send)
            status = 'completed' if result.status == 'completed' else 'failed'
            self.db.update_record(identifier, status=status, finished_at=now(), final_response=result.final_response,
                                  error=result.error or ('' if status == 'completed' else f'Turn {result.status}'))
            self.emit({'kind': status, 'worker_id': worker.id, 'record_id': identifier,
                       'text': f'Issue #{issue.number}: {status}'})
            return status
        except WorkerPaused as exc:
            self.db.update_record(identifier, status='queued', error=str(exc))
            return 'paused'
        except ThreadBusy as exc:
            attempts = record['attempts'] + 1
            status = 'queued' if attempts < 6 else 'failed'
            self.db.update_record(identifier, status=status, attempts=attempts, error=str(exc),
                                  **({'finished_at': now()} if status == 'failed' else {}))
            self.emit({'kind': 'waiting', 'worker_id': worker.id, 'text': str(exc)})
            return 'busy' if status == 'queued' else 'failed'
        except RecoveryRequired as exc:
            self.db.update_record(identifier, status='recovery_required', error=str(exc))
            self.emit({'kind': 'error', 'worker_id': worker.id, 'record_id': identifier, 'text': str(exc)})
            return 'recovery_required'
        except Exception as exc:
            # If ack was persisted, failure is uncertain until the turn is inspected.
            current = self.db.record(identifier)
            status = 'recovery_required' if current['status'] == 'dispatched' else 'failed'
            self.db.update_record(identifier, status=status, error=redact(str(exc)),
                                  **({'finished_at': now()} if status == 'failed' else {}))
            self.emit({'kind': 'error', 'worker_id': worker.id, 'record_id': identifier, 'text': redact(str(exc))})
            return status

    def redispatch(self, identifier):
        record = self.db.record(identifier)
        if not record:
            raise DispatchError('历史记录不存在')
        worker = self.db.get_worker(record['worker_id'])
        if not worker or not worker.enabled:
            raise DispatchError('请先启用此 Worker')
        issue = self.github.get_issue(worker.repository, record['issue_number'])
        if not issue.matches(worker):
            raise DispatchError('Issue 已不符合当前分配规则，不能重新派送')
        return self.db.reserve(worker, issue, force=True)

    def check_recovery(self, identifier):
        record = self.db.record(identifier)
        if record['status'] != 'recovery_required':
            raise DispatchError('此任务无需恢复检查')
        if not record['turn_id']:
            return '发送结果未知，未保存 Turn ID。请查看目标会话中的 Dispatch ID，再标记已处理或手动重新派送。'
        turn = self.codex.inspect_turn(record['target_thread_id'], record['turn_id'])
        if not turn:
            return '暂未在会话中找到该 Turn；仍保持恢复检查，不自动重新发送。'
        # A second process can expose synthetic interrupted history for an actually live turn.
        # Only a persisted completed turn is a positive recovery signal after restart.
        if turn['status'] == 'completed':
            texts = [x.get('text', '') for x in turn.get('items', [])
                     if x.get('type') == 'agentMessage' and x.get('phase') in (None, 'final_answer')]
            self.db.update_record(identifier, status='completed', finished_at=now(),
                                  final_response=texts[-1] if texts else '', error='')
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
