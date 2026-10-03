import json
from unittest.mock import Mock

import pytest
from codex_dispatcher.domain.models import DispatchError, RecoveryRequired, ThreadBusy, WorkerPaused
from codex_dispatcher.services.desktop_bridge import DesktopCodexService, desktop_endpoint, register_desktop_context


def adapter(worker, response=None, *, status='idle'):
    sdk = Mock()
    sdk.read_thread.return_value = {'id': worker.target_thread_id, 'cwd': worker.target_project,
                                    'status': {'type': 'active'}}
    def reply(method, params, **kwargs):
        value = sdk.read_thread.return_value.copy()
        value['status'] = {'type': status}
        if params['tool'] == 'read_thread':
            return {'success': True, 'contentItems': [{'type': 'inputText', 'text': json.dumps({'thread': value})}]}
        return response or {'success': True, 'contentItems': [
            {'type': 'inputText', 'text': json.dumps({'threadId': worker.target_thread_id})}]}
    request = Mock(side_effect=reply)
    return DesktopCodexService('configured-caller', sdk=sdk, request=request), sdk, request


def test_idle_desktop_thread_receives_followup_without_resume(worker):
    service, sdk, request = adapter(worker)
    acknowledgments = []
    result = service.send_task(worker.target_thread_id, 'configured notification', worker.target_project,
                               trusted=True, on_started=acknowledgments.append)
    params = request.call_args.args[1]
    assert params['tool'] == 'send_message_to_thread'
    assert params['arguments'] == {'threadId': worker.target_thread_id, 'hostId': 'local',
                                    'prompt': 'configured notification'}
    assert request.call_args.kwargs['submitting'] is True
    assert acknowledgments == [''] and result.status == 'accepted'
    sdk.send_task.assert_not_called()
    sdk._check_busy.assert_not_called()
    sdk.interrupt.assert_not_called()


def test_desktop_configuration_reads_owner_without_sdk_routing(worker):
    service, sdk, request = adapter(worker)
    sdk.read_thread.side_effect = DispatchError('workspace routing discovery timed out')
    data = service.read_thread(worker.target_thread_id)
    assert data['id'] == worker.target_thread_id and data['cwd'] == worker.target_project
    sdk.read_thread.assert_not_called()
    assert request.call_args.args[1]['tool'] == 'read_thread'


def test_routing_failure_before_delivery_stays_queued_and_then_delivers_once(core, worker, monkeypatch):
    monkeypatch.setattr('codex_dispatcher.services.codex_service.time.sleep', lambda _: None)
    service, _, request = adapter(worker)
    original_reply = request.side_effect
    request.side_effect = DispatchError('workspace routing discovery timed out')
    core.service.codex = service
    identifier = core.service.discover(worker)[0]
    for _ in range(7):
        assert core.service.process_record(identifier) == 'busy'
    assert core.db.record(identifier)['status'] == 'queued'
    assert all(call.args[1]['tool'] == 'read_thread' for call in request.call_args_list)
    request.side_effect = original_reply
    assert core.service.process_record(identifier) == 'notified'
    assert len([call for call in request.call_args_list if call.args[1]['tool'] == 'send_message_to_thread']) == 1


def test_routing_text_in_uncertain_send_error_never_retries(worker, monkeypatch):
    monkeypatch.setattr('codex_dispatcher.services.codex_service.time.sleep', lambda _: None)
    response = {'success': False, 'contentItems': [{'type': 'inputText', 'text': 'workspace routing discovery timed out'}]}
    service, _, request = adapter(worker, response)
    with pytest.raises(RecoveryRequired):
        service.send_task(worker.target_thread_id, 'notification', worker.target_project, trusted=True)
    assert request.call_count == 2


@pytest.mark.parametrize('response', [
    {'success': True, 'contentItems': [{'type': 'inputText', 'text': '{"threadId":"different"}'}]},
    {'success': True, 'contentItems': []},
    {'success': False, 'contentItems': [{'type': 'inputText', 'text': 'rejected'}]},
])
def test_unknown_desktop_receipt_never_acknowledges_or_retries(worker, response):
    service, _, request = adapter(worker, response)
    ack = Mock()
    with pytest.raises(RecoveryRequired):
        service.send_task(worker.target_thread_id, 'notification', worker.target_project,
                          trusted=True, on_started=ack)
    ack.assert_not_called()
    assert request.call_count == 2


def test_desktop_pause_and_project_guard_run_before_submission(worker):
    service, sdk, request = adapter(worker)
    with pytest.raises(WorkerPaused):
        service.send_task(worker.target_thread_id, 'notification', worker.target_project,
                          trusted=True, can_send=lambda: False)
    assert request.call_count == 1 and request.call_args.args[1]['tool'] == 'read_thread'
    sdk.read_thread.return_value['id'] = 'different'
    with pytest.raises(DispatchError):
        service.send_task(worker.target_thread_id, 'notification', worker.target_project, trusted=True)
    assert request.call_count == 2
    assert all(call.args[1]['tool'] == 'read_thread' for call in request.call_args_list)


def test_desktop_ack_is_durable_without_fabricating_turn_id(core, worker):
    service, _, request = adapter(worker)
    core.service.codex = service
    identifier = core.service.discover(worker)[0]
    assert core.service.process_record(identifier) == 'notified'
    record = core.db.record(identifier)
    assert record['turn_id'] == '' and record['dispatch_time'] and record['finished_at']
    assert core.service.discover(worker) == []
    assert request.call_count == 2
    assert not core.db.thread_blocked(worker.target_thread_id)


def test_busy_desktop_notification_stays_queued_until_idle_even_after_six_checks(core, worker):
    service, sdk, request = adapter(worker, status='active')
    core.service.codex = service
    identifier = core.service.discover(worker)[0]
    for _ in range(8):
        assert core.service.process_record(identifier) == 'busy'
    assert core.db.record(identifier)['status'] == 'queued'
    assert core.db.record(identifier)['attempts'] == 8
    assert all(call.args[1]['tool'] == 'read_thread' for call in request.call_args_list)
    sdk.interrupt.assert_not_called()
    idle, _, _ = adapter(worker)
    core.service.codex = idle
    assert core.service.process_record(identifier) == 'notified'
    assert core.service.discover(worker) == []


def test_bridge_registration_uses_only_provided_context_and_no_pipe_secret(tmp_path, monkeypatch):
    caller = '01a0fcdf-ef68-77e1-90ff-8cba7a60521e'
    monkeypatch.setenv('CODEX_THREAD_ID', caller)
    monkeypatch.setenv('CODEX_APP_TOOLS_PIPE_PATH', 'local-pipe')
    assert register_desktop_context(tmp_path) == caller
    assert json.loads((tmp_path / 'desktop-bridge.json').read_text()) == {'caller_thread_id': caller, 'pipe_path':'local-pipe'}
    monkeypatch.delenv('CODEX_THREAD_ID')
    monkeypatch.delenv('CODEX_APP_TOOLS_PIPE_PATH')
    assert register_desktop_context(tmp_path) == caller


def test_desktop_restart_discovers_new_pipe_without_reusing_stale_endpoint(monkeypatch):
    monkeypatch.setenv('CODEX_APP_TOOLS_PIPE_PATH', '\\\\.\\pipe\\codex-browser-use-old')
    monkeypatch.setattr('os.listdir', lambda _: ['unrelated', 'codex-browser-use-new'])
    assert desktop_endpoint() == '\\\\.\\pipe\\codex-browser-use-new'
    monkeypatch.setattr('os.listdir', lambda _: [])
    with pytest.raises(ThreadBusy) as caught:
        desktop_endpoint()
    assert caught.value.keep_queued


def test_pipe_catalog_distinguishes_browser_control_from_desktop_notifications(monkeypatch):
    monkeypatch.delenv('CODEX_APP_TOOLS_PIPE_PATH', raising=False)
    monkeypatch.setattr('os.listdir', lambda _: ['codex-browser-use-browser', 'codex-browser-use-app'])
    def catalog(method, params, **kwargs):
        assert method == 'tools/list' and not kwargs.get('submitting')
        return {'tools': [{'name':'send_message_to_thread','namespace':'codex_app'}]} if kwargs['preferred_endpoint'].endswith('-app') else {'tools': []}
    monkeypatch.setattr('codex_dispatcher.services.desktop_bridge.pipe_request', catalog)
    assert desktop_endpoint() == '\\\\.\\pipe\\codex-browser-use-app'
