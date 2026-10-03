"""Local transport used by the installed Codex App Tools plugin.

This is an experimental desktop integration, not a public standalone SDK API.
Only explicitly configured Workers may send; GitHub never supplies thread IDs.
"""
from __future__ import annotations

import json
import os
from functools import partial
from pathlib import Path
import struct
import time
import uuid

from codex_dispatcher.domain.models import DispatchError, RecoveryRequired, ThreadBusy, WorkerPaused, same_path
from .codex_service import CodexService, RunResult

MAX_FRAME = 8 * 1024 * 1024


def register_desktop_context(directory: Path):
    caller = os.environ.get('CODEX_THREAD_ID', '')
    if caller and os.environ.get('CODEX_APP_TOOLS_PIPE_PATH'):
        uuid.UUID(caller)
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / 'desktop-bridge.json'
        temporary = path.with_suffix('.tmp')
        temporary.write_text(json.dumps({'caller_thread_id': caller,
                                        'pipe_path': os.environ['CODEX_APP_TOOLS_PIPE_PATH']}), encoding='utf-8')
        temporary.replace(path)
    path = directory / 'desktop-bridge.json'
    if path.is_file():
        caller = json.loads(path.read_text(encoding='utf-8'))['caller_thread_id']
        uuid.UUID(caller)
        return caller
    return None


def desktop_endpoint(preferred=None):
    supplied = preferred or os.environ.get('CODEX_APP_TOOLS_PIPE_PATH')
    # The desktop pipe changes after restarting Codex. Discover only its known
    # local pipe prefix, never scan TCP endpoints or read authentication tokens.
    try:
        pipes = [name for name in os.listdir('\\\\.\\pipe\\')
                 if name.startswith('codex-browser-use-')]
    except OSError as exc:
        raise ThreadBusy('Codex 桌面桥接暂不可用，通知保留在队列中。', keep_queued=True) from exc
    if supplied and supplied.rsplit('\\', 1)[-1] in pipes:
        return '\\\\.\\pipe\\' + supplied.rsplit('\\', 1)[-1]
    if not pipes:
        raise ThreadBusy('Codex 桌面应用尚未打开，通知保留在队列中。', keep_queued=True)
    if len(pipes) != 1:
        # Browser control uses the same pipe prefix. Probe only the read-only
        # catalog; never send a notification until the app-tools pipe is known.
        matches = []
        for name in pipes[:8]:
            endpoint = '\\\\.\\pipe\\' + name
            try:
                catalog = pipe_request('tools/list', {'threadStartKind': 'all'},
                                       preferred_endpoint=endpoint, timeout=1)
                if any(tool.get('name') == 'send_message_to_thread' and tool.get('namespace') == 'codex_app'
                       for tool in catalog.get('tools', [])):
                    matches.append(endpoint)
            except DispatchError:
                continue
        if len(matches) == 1:
            return matches[0]
        raise DispatchError('没有找到唯一的 Codex 桌面通知接口，请打开 Codex 桌面应用后重试。')
    return '\\\\.\\pipe\\' + pipes[0]


def pipe_request(method, params, *, submitting=False, timeout=40, preferred_endpoint=None):
    import ctypes
    import msvcrt
    from ctypes import wintypes
    endpoint = desktop_endpoint(preferred_endpoint)
    payload = json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params},
                         ensure_ascii=False).encode('utf-8')
    if len(payload) > MAX_FRAME:
        raise DispatchError('桌面桥接请求过大')
    written = False
    try:
        with open(endpoint, 'r+b', buffering=0) as pipe:
            kernel = ctypes.WinDLL('kernel32', use_last_error=True)
            peek = kernel.PeekNamedPipe
            peek.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
                             ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
            peek.restype = wintypes.BOOL
            handle = msvcrt.get_osfhandle(pipe.fileno())
            deadline = time.monotonic() + timeout
            # A partial write may have crossed the submission boundary.
            written = True
            frame = struct.pack('<I', len(payload)) + payload
            offset = 0
            while offset < len(frame):
                count = pipe.write(frame[offset:])
                if not count:
                    raise OSError('桌面桥接连接已关闭')
                offset += count

            def read_exact(size):
                output = bytearray()
                while len(output) < size:
                    available = wintypes.DWORD()
                    if not peek(handle, None, 0, None, ctypes.byref(available), None):
                        raise OSError('桌面桥接连接已关闭')
                    if time.monotonic() >= deadline:
                        raise TimeoutError('桌面桥接响应超时')
                    if not available.value:
                        time.sleep(.02)
                        continue
                    block = pipe.read(min(size - len(output), available.value))
                    if not block:
                        raise OSError('桌面桥接连接已关闭')
                    output.extend(block)
                return bytes(output)

            size = struct.unpack('<I', read_exact(4))[0]
            if size > MAX_FRAME:
                raise ValueError('桌面桥接响应过大')
            response = json.loads(read_exact(size).decode('utf-8'))
            if response.get('id') != 1 or response.get('jsonrpc') != '2.0':
                raise ValueError('桌面桥接响应格式无效')
            if 'error' in response:
                if submitting:
                    raise RecoveryRequired('桌面应用返回错误，发送结果待确认；不会自动重发。')
                raise DispatchError(response['error'].get('message', '桌面桥接请求被拒绝'))
            return response['result']
    except DispatchError:
        raise
    except Exception as exc:
        if submitting and written:
            raise RecoveryRequired('桌面发送结果不确定，请检查原会话中的通知 ID；不会自动重发。') from exc
        if not written and isinstance(exc, OSError):
            raise ThreadBusy('Codex 桌面桥接暂不可用，通知保留在队列中。', keep_queued=True) from exc
        raise DispatchError('Codex 桌面桥接连接失败：' + str(exc)) from exc


class DesktopCodexService:
    """Metadata through the SDK, delivery through the original desktop owner."""
    def __init__(self, caller_thread_id, *, sdk=None, request=None, endpoint_hint=None):
        self.caller_thread_id = caller_thread_id
        self.sdk = sdk or CodexService()
        self.request = request or partial(pipe_request, preferred_endpoint=endpoint_hint)

    def __getattr__(self, name):
        return getattr(self.sdk, name)

    def check_connection(self):
        result = self.request('tools/list', {'threadStartKind': 'all'})
        if not any(t.get('name') == 'send_message_to_thread' and t.get('namespace') == 'codex_app'
                   for t in result.get('tools', [])):
            raise DispatchError('此版本 Codex 桌面应用未提供会话通知接口')
        return self.sdk.check_connection()

    def app_tool(self, tool, arguments, *, submitting=False):
        if tool not in ('read_thread', 'send_message_to_thread'):
            raise DispatchError('桌面桥接只允许读取会话状态和发送通知')
        response = self.request('tools/call', {
            'namespace': 'codex_app', 'tool': tool, 'arguments': arguments,
            'callerSource': 'codex', 'threadId': self.caller_thread_id,
            'callId': 'dispatcher-' + str(uuid.uuid4()), 'turnId': 'dispatcher-notification',
        }, submitting=submitting)
        error_type = RecoveryRequired if submitting else DispatchError
        if response.get('success') is not True:
            raise error_type('桌面应用未确认请求：' + '\n'.join(
                item.get('text', '') for item in response.get('contentItems', [])))
        try:
            values = [json.loads(item['text']) for item in response.get('contentItems', [])
                      if item.get('type') == 'inputText']
            if len(values) != 1 or not isinstance(values[0], dict):
                raise ValueError('Invalid receipt')
            return values[0]
        except Exception as exc:
            raise error_type('桌面应用未返回有效回执；发送请求不会自动重发。') from exc

    def read_desktop_thread(self, thread_id):
        return self.app_tool('read_thread', {'threadId': thread_id, 'hostId': 'local',
                                            'turnLimit': 1, 'includeOutputs': False})

    def send_task(self, thread_id, prompt, project_path, *, trusted=False,
                  on_started=lambda turn_id: None, can_send=lambda: True, **kwargs):
        if not trusted:
            raise DispatchError('桌面桥接只接收用户配置的通知，不转发外部任务原文')
        data = self.read_desktop_thread(thread_id)['thread']
        if data.get('id') != thread_id or not same_path(data.get('cwd', ''), project_path):
            raise DispatchError('目标会话或项目目录不一致')
        if not can_send():
            raise WorkerPaused('监测已停止，通知保留在队列中。')
        # Persist the notification while busy without interrupting the ongoing
        # task. Queue retries read the owning desktop's actual live status.
        if data.get('status', {}).get('type') == 'active':
            raise ThreadBusy('目标会话正在工作，通知已排队，空闲后自动发送。', keep_queued=True)
        receipt = self.app_tool('send_message_to_thread',
                               {'threadId': thread_id, 'hostId': 'local', 'prompt': prompt}, submitting=True)
        if receipt.get('threadId') != thread_id:
            raise RecoveryRequired('桌面应用未返回匹配的接收回执，请检查原会话；不会自动重发。')
        # Desktop ACK is a receipt for a follow-up, not an invented Turn ID.
        on_started('')
        return RunResult('', 'accepted', '')


def create_codex_service(directory):
    caller = register_desktop_context(Path(directory)) if os.name == 'nt' else None
    if caller:
        config = json.loads((Path(directory) / 'desktop-bridge.json').read_text(encoding='utf-8'))
        return DesktopCodexService(caller, endpoint_hint=config.get('pipe_path'))
    return CodexService()
