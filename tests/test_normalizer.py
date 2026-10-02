import json
from types import SimpleNamespace
import pytest
from codex_dispatcher.domain.models import DispatchError
from codex_dispatcher.services.codex_service import CodexService


class NormalizeClient:
    def __init__(self, profile='dispatcher-normalize', tool=False):
        self.profile, self.tool, self.turns, self.interrupted, self.closed = profile, tool, [], False, False
        self.events = iter(([SimpleNamespace(method='item/started', payload={'item':{'type':'commandExecution'}})] if tool else []) + [
            SimpleNamespace(method='item/completed', payload={'item':{'type':'agentMessage','text':json.dumps({
                'dispatch':False,'summary':'summary','requirements':[], 'acceptance_criteria':[], 'warnings':[]})}}),
            SimpleNamespace(method='turn/completed', payload={'turn':{'status':'completed'}})])
    def start(self): pass
    def initialize(self): pass
    def close(self): self.closed = True
    def request(self, method, params, **kwargs):
        if method == 'config/read':
            return {'config':{'mcp_servers':{'node_repl':{}}, 'plugins':{}}}
        self.start_params = params
        return {'thread':{'id':'ephemeral'}, 'activePermissionProfile':{'id':self.profile}}
    def turn_start(self, tid, inputs, params):
        self.turns.append((tid,inputs,params))
        return {'turn':{'id':'normalize-turn'}}
    def next_turn_notification(self, tid): return next(self.events)
    def turn_interrupt(self, *args): self.interrupted = True


def normalizer(profile='dispatcher-normalize', tool=False):
    reader, client = NormalizeClient(), NormalizeClient(profile, tool)
    clients, configs = iter([reader,client]), []
    def factory(**kwargs):
        configs.append(kwargs['config'])
        return next(clients)
    return CodexService(client_factory=factory), client, configs


def test_normalizer_isolated_runtime_and_authoritative_assignment(issue, worker):
    service, client, configs = normalizer()
    result = service.normalize(issue, 'dynamic-choice')
    assert result['dispatch'] is True
    config = configs[-1]
    assert config.cwd != worker.target_project
    assert config.experimental_api is True
    assert 'mcp_servers.node_repl.enabled=false' in config.config_overrides
    assert 'features.shell_tool=false' in config.config_overrides
    assert any(':root' in value and 'deny' in value for value in config.config_overrides)
    assert client.start_params['ephemeral'] is True
    assert client.turns[0][1] == []
    assert 'developerInstructions' not in client.turns[0][2]
    assert client.closed


def test_normalizer_refuses_unconfirmed_permission_profile(issue):
    service, client, _ = normalizer(profile=':danger-full-access')
    with pytest.raises(DispatchError, match='未确认'):
        service.normalize(issue, 'dynamic-choice')
    assert client.turns == []
    assert client.closed


def test_normalizer_tool_attempt_interrupts_and_fails(issue):
    service, client, _ = normalizer(tool=True)
    with pytest.raises(DispatchError, match='工具'):
        service.normalize(issue, 'dynamic-choice')
    assert client.interrupted
    assert client.closed
