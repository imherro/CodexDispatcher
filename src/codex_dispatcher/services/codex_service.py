"""The only production module importing Codex SDK or depending on its protocol."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from functools import wraps
from pathlib import Path
import threading
import time

from openai_codex import CodexConfig, JsonRpcError, TransportClosedError
from openai_codex.client import CodexClient
from openai_codex.generated.v2_all import ThreadSourceKind

from codex_dispatcher.domain.models import DispatchError, RecoveryRequired, ThreadBusy, ThreadInfo, WorkerPaused, same_path
from .security import redact


def routing_timeout(error):
    return 'workspace routing discovery timed out' in str(error).casefold()


def retry_routing_read(action):
    """Retry only read-only operations after this specific discovery failure."""
    @wraps(action)
    def read(*args, **kwargs):
        for attempt in range(3):
            try:
                return action(*args, **kwargs)
            except DispatchError as exc:
                if not routing_timeout(exc):
                    raise
                if attempt == 2:
                    raise ThreadBusy('Codex 暂时无法定位会话，已重试 3 次。请稍后重试；待发送通知会保留在队列中。',
                                     keep_queued=True) from exc
                time.sleep(.25 * (attempt + 1))
    return read


def wire(value):
    return value.model_dump(mode='json', by_alias=True) if hasattr(value, 'model_dump') else value


@dataclass
class RunResult:
    turn_id: str
    status: str
    final_response: str
    error: str = ''


class CodexService:
    """Public low-level SDK client: read/list/resume/start/stream all in one adapter.

    Dedicated SDK app-servers cannot determine another app-server's live ownership.
    They must be used with exclusive human ownership while monitoring a Thread.
    """
    def __init__(self, client_factory=CodexClient, config=None, *, call_timeout=40, turn_timeout=3600):
        self.client_factory = client_factory
        self.config = config or CodexConfig(client_name='codex_dispatcher', client_title='Codex Dispatcher')
        self.call_timeout = call_timeout
        self.turn_timeout = turn_timeout
        self._active = {}
        self._guard = threading.RLock()

    @staticmethod
    def _approval_handler(method, params):
        # SDK 0.160.0's default handler ACCEPTS escalations. Explicitly fail closed.
        if method in ('item/commandExecution/requestApproval', 'item/fileChange/requestApproval'):
            return {'decision': 'decline'}
        if method == 'item/permissions/requestApproval':
            return {'permissions': {}, 'scope': 'turn'}
        if method == 'item/tool/call':
            return {'success': False, 'contentItems': [{'type': 'inputText', 'text': 'Dispatcher does not execute dynamic tools.'}]}
        if method == 'item/tool/requestUserInput':
            return {'answers': {}}
        if method == 'mcpServer/elicitation/request':
            return {'action': 'decline', 'content': None}
        return {}

    def _new_client(self, config=None):
        return self.client_factory(config=config or self.config, approval_handler=self._approval_handler)

    def _bounded(self, action, *, on_timeout=None):
        result, done = [], threading.Event()
        def run():
            try:
                result.append((True, action()))
            except Exception as exc:
                result.append((False, exc))
            finally:
                done.set()
        threading.Thread(target=run, daemon=True).start()
        if not done.wait(self.call_timeout):
            if on_timeout:
                on_timeout()
            raise DispatchError('Codex runtime 响应超时，请检查本地安装和登录状态')
        success, value = result[0]
        if not success:
            raise value
        return value

    @contextmanager
    def _reader(self):
        client = self._new_client()
        try:
            self._bounded(lambda: (client.start(), client.initialize()), on_timeout=client.close)
            yield client
        except (JsonRpcError, TransportClosedError, OSError) as exc:
            raise DispatchError(redact(str(exc))) from exc
        finally:
            client.close()  # reader clients have never started a turn

    @retry_routing_read
    def check_connection(self):
        with self._reader() as client:
            account = wire(self._bounded(client.account_read, on_timeout=client.close))
            if account.get('requiresOpenaiAuth', True) and not account.get('account'):
                raise DispatchError('Codex 未登录，请在 Codex Desktop 或 codex login 中完成登录')
            return True

    @retry_routing_read
    def list_threads(self, project_path=None):
        with self._reader() as client:
            params = {'limit': 100, 'sourceKinds': [x.value for x in ThreadSourceKind], 'sortKey': 'updated_at'}
            if project_path:
                params['cwd'] = str(Path(project_path).resolve())
            result, seen = [], set()
            while True:
                page = wire(self._bounded(lambda: client.thread_list(params), on_timeout=client.close))
                result.extend(ThreadInfo.from_wire(row) for row in page.get('data', []))
                cursor = page.get('nextCursor')
                if not cursor:
                    break
                if cursor in seen:
                    raise DispatchError('Codex 会话分页返回重复游标')
                seen.add(cursor)
                params['cursor'] = cursor
            # Also defend against server versions that accept but ignore cwd filtering.
            return [t for t in result if not project_path or same_path(t.cwd, project_path)]

    @retry_routing_read
    def read_thread(self, thread_id, *, include_turns=False):
        with self._reader() as client:
            return wire(self._bounded(lambda: client.thread_read(thread_id, include_turns=include_turns), on_timeout=client.close))['thread']

    @staticmethod
    def _check_project(data, project_path, allow_mismatch):
        cwd = data.get('cwd') or ''
        if not cwd:
            raise DispatchError('会话缺少 cwd 元数据，无法验证目标项目')
        if not allow_mismatch and not same_path(cwd, project_path):
            raise DispatchError(f'目标 Thread 似乎不属于当前项目：\nThread: {cwd}\n项目: {project_path}')

    @staticmethod
    def _check_busy(data):
        if data.get('status', {}).get('type') == 'active' or any(t.get('status') == 'inProgress' for t in data.get('turns', [])):
            raise ThreadBusy('目标会话正在运行，任务已排队。', keep_queued=True)

    @staticmethod
    def _rpc_error(exc):
        message = exc.message.casefold()
        if routing_timeout(exc):
            return ThreadBusy('Codex 暂时无法定位会话，通知已排队，稍后自动重试。', keep_queued=True)
        if any(marker in message for marker in ('active writer', 'another writer')):
            return ThreadBusy('目标会话的写入权由 Codex 桌面应用或其他进程持有，空闲时也可能被占用。'
                              '请启用桌面桥接，向原应用发送通知。')
        if any(marker in message for marker in ('busy', 'already running', 'conflict')):
            return ThreadBusy('目标会话暂不能接收通知，任务已排队。')
        return DispatchError(redact(str(exc)))

    def validate_thread(self, thread_id, project_path, allow_mismatch=False):
        # Configuration validation must not acquire another app's writer lease.
        # A readable saved Thread can be configured while Desktop owns it; only
        # the actual dispatch attempts resume and handles the lease conflict.
        data = self.read_thread(thread_id)
        if data.get('id') != thread_id:
            raise DispatchError('Codex 返回的 Thread ID 与配置不一致')
        self._check_project(data, project_path, allow_mismatch)
        return ThreadInfo.from_wire(data)

    def resume_thread(self, thread_id, project_path, allow_mismatch=False):
        # Read-only validation facade; a dispatch owns its client for the whole turn.
        return self.validate_thread(thread_id, project_path, allow_mismatch)

    def send_task(self, thread_id, task, project_path, *, allow_mismatch=False,
                  on_started=lambda tid: None, on_event=lambda event: None, trusted=False, can_send=lambda: True):
        client = self._new_client()
        submitted = False
        terminal = False
        try:
            self._bounded(lambda: (client.start(), client.initialize()), on_timeout=client.close)
            data = wire(self._bounded(lambda: client.thread_read(thread_id, include_turns=True), on_timeout=client.close))['thread']
            self._check_project(data, project_path, allow_mismatch)
            self._check_busy(data)
            resumed = wire(self._bounded(lambda: client.thread_resume(thread_id), on_timeout=client.close))
            if resumed['thread']['id'] != thread_id:
                raise DispatchError('Codex 恢复的 Thread ID 与配置不一致')
            self._check_project(resumed['thread'], project_path, allow_mismatch)
            self._check_busy(resumed['thread'])
            if not can_send():
                raise WorkerPaused('监视已停止，任务保留在队列中。')
            # Production uses a fixed user-authorized wake-up instruction.
            # It contains only a validated locator, never Issue body/title/comments.
            params = None if trusted else {'toolOutput': {'name': 'github_issue', 'namespace': 'codex_dispatcher', 'output': task}}
            submitted = True  # persist intent before crossing the non-idempotent RPC boundary
            try:
                started = wire(self._bounded(lambda: client.turn_start(thread_id, task if trusted else [], params)))
            except JsonRpcError as exc:
                # Explicit rejection is safe to queue, unlike timeouts / transport drops.
                submitted = False
                raise self._rpc_error(exc) from exc
            turn_id = started['turn']['id']
            with self._guard:
                self._active[thread_id] = (client, turn_id)
            on_started(turn_id)
            result = self._consume(client, turn_id, on_event)
            terminal = True
            return result
        except (ThreadBusy, RecoveryRequired):
            raise
        except Exception as exc:
            if submitted and not terminal:
                # Keep child runtime alive on uncertain submission / stream loss.
                with self._guard:
                    self._active[thread_id] = (client, locals().get('turn_id'))
                raise RecoveryRequired(redact(f'需要恢复检查：{exc}')) from exc
            if isinstance(exc, DispatchError):
                raise
            if isinstance(exc, JsonRpcError):
                raise self._rpc_error(exc) from exc
            raise DispatchError(redact(str(exc))) from exc
        finally:
            if not submitted or terminal:
                with self._guard:
                    self._active.pop(thread_id, None)
                client.close()

    stream_task = send_task

    def _consume(self, client, turn_id, on_event, on_raw_event=lambda method, payload: None):
        deadline = time.monotonic() + self.turn_timeout
        final, last_message = '', ''
        while True:
            # No kill on timeout. Caller records an uncertain task and blocks that queue.
            if time.monotonic() >= deadline:
                raise RecoveryRequired('会话运行超时，需要恢复检查；应用未终止任务。')
            event = self._bounded(lambda: client.next_turn_notification(turn_id))
            method, payload = event.method, wire(event.payload)
            on_raw_event(method, payload)
            if method == 'item/completed':
                item = payload.get('item', {})
                if item.get('type') == 'agentMessage':
                    last_message = item.get('text', '')
                    if item.get('phase') == 'final_answer':
                        final = last_message
            visible = self.visible_event(method, payload)
            if visible:
                on_event(visible)
            if method == 'turn/completed':
                turn = payload['turn']
                return RunResult(turn_id, turn['status'], final or last_message,
                                 redact((turn.get('error') or {}).get('message', '')))

    @staticmethod
    def visible_event(method, payload):
        # Whitelist excludes reasoning / raw chain-of-thought and raw tool arguments.
        if method == 'item/agentMessage/delta':
            return {'kind': 'message', 'text': redact(payload.get('delta', ''))}
        if method in ('turn/started', 'turn/completed'):
            return {'kind': 'status', 'text': method + ': ' + payload.get('turn', {}).get('status', '')}
        if method in ('item/started', 'item/completed'):
            item = payload.get('item', {})
            kind = item.get('type')
            if kind in ('commandExecution', 'fileChange', 'mcpToolCall', 'dynamicToolCall', 'webSearch'):
                text = kind + ' / ' + method.rsplit('/', 1)[-1]
                if kind == 'commandExecution':
                    text += ' / ' + redact(item.get('command', ''))[:500]
                if kind == 'fileChange':
                    text += ' / ' + ', '.join(c.get('path', '') for c in item.get('changes', []))[:500]
                return {'kind': 'tool', 'text': text}
        return None

    def interrupt(self, thread_id):
        with self._guard:
            active = self._active.get(thread_id)
        if not active or not active[1]:
            raise DispatchError('没有可安全中断的已知 Turn ID，请先检查状态')
        return self._bounded(lambda: active[0].turn_interrupt(thread_id, active[1]))

    def inspect_turn(self, thread_id, turn_id):
        with self._guard:
            active = self._active.get(thread_id)
        if active:
            data = wire(self._bounded(lambda: active[0].thread_read(thread_id, include_turns=True)))['thread']
        else:
            data = self.read_thread(thread_id, include_turns=True)
        return next((t for t in data.get('turns', []) if t.get('id') == turn_id), None)

    def release_terminal(self, thread_id):
        with self._guard:
            active = self._active.pop(thread_id, None)
        if active:
            active[0].close()

    def active_threads(self):
        with self._guard:
            return list(self._active)
