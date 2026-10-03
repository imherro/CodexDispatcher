import subprocess
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from codex_dispatcher.domain.models import DispatchError
from codex_dispatcher.services.github_service import GitHubService, GitHubUnavailable
from codex_dispatcher.services.monitor_service import MonitorService


def failed(message):
    return SimpleNamespace(returncode=1, stdout='', stderr=message)


@pytest.fixture(autouse=True)
def no_retry_sleep(monkeypatch):
    monkeypatch.setattr('codex_dispatcher.services.github_service.time.sleep', lambda _: None)


@pytest.mark.parametrize('failure', [failed('Post https://api.github.com/graphql: EOF'),
                                    failed('HTTP 503: service unavailable'),
                                    subprocess.TimeoutExpired('gh', 1)])
def test_read_only_queries_retry_transient_errors(failure):
    runner = Mock(side_effect=[failure, SimpleNamespace(returncode=0, stdout='[]', stderr='')])
    assert GitHubService('gh', runner).list_repositories() == []
    assert runner.call_count == 2


def test_retry_is_bounded_and_remaining_time_is_used():
    runner = Mock(return_value=failed('unexpected EOF'))
    with pytest.raises(GitHubUnavailable):
        GitHubService('gh', runner).list_repositories()
    assert runner.call_count == 3
    assert all(0 < call.kwargs['timeout'] <= 45 for call in runner.call_args_list)


@pytest.mark.parametrize('error', ['HTTP 401: bad credentials', 'HTTP 404: not found', 'API rate limit exceeded'])
def test_permanent_and_rate_limit_errors_are_not_retried(error):
    runner = Mock(return_value=failed(error))
    with pytest.raises(DispatchError) as caught:
        GitHubService('gh', runner).list_repositories()
    assert not isinstance(caught.value, GitHubUnavailable)
    assert runner.call_count == 1


def test_mutation_is_never_retried_after_eof():
    runner = Mock(return_value=failed('EOF'))
    with pytest.raises(DispatchError):
        GitHubService('gh', runner)._run(['api', '-X', 'POST', 'repos/owner/repo/issues/1/comments'])
    assert runner.call_count == 1


def test_network_failure_before_send_keeps_notice_then_sends_once(core, worker):
    identifier = core.service.discover(worker)[0]
    issue = core.github.get_issue.return_value
    core.github.get_issue.side_effect = GitHubUnavailable('temporary EOF')
    for _ in range(7):
        assert core.service.process_record(identifier) == 'busy'
    assert core.db.record(identifier)['status'] == 'queued'
    core.codex.send_task.assert_not_called()
    core.github.get_issue.side_effect = None
    core.github.get_issue.return_value = issue
    assert core.service.process_record(identifier) == 'notified'
    core.codex.send_task.assert_called_once()


def test_temporary_outage_does_not_stop_monitor_after_five_polls(core, worker):
    monitor = MonitorService(core.service, Mock())
    attempts = []
    def check(*args, **kwargs):
        attempts.append(1)
        if len(attempts) < 7:
            raise GitHubUnavailable('EOF')
    monitor.check_now = check
    stop = Mock()
    stop.is_set.side_effect = lambda: len(attempts) >= 7
    monitor.stop = Mock()
    monitor._loop(worker, stop)
    assert len(attempts) == 7
    monitor.stop.assert_not_called()

