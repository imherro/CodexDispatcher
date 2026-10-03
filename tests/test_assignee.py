from dataclasses import replace
import json
from types import SimpleNamespace

import pytest

from codex_dispatcher.domain.models import Worker
from codex_dispatcher.services.github_service import GitHubService
from test_mentions import row, comment


@pytest.mark.parametrize('login', ['actual-user', 'Actual-User', ' Actual-User '])
def test_assignee_account_normalization_and_exact_match(worker, issue, login):
    worker = replace(worker, assignment_mode='assignee', assignment_value=login)
    worker.validate()
    assert not worker.assignment_value.startswith('@')
    assert replace(issue, assignees=['someone', 'ACTUAL-USER']).matches(worker)
    assert not replace(issue, body='@actual-user', assignees=['actual-user-other']).matches(worker)
    assert not replace(issue, assignees=[]).matches(worker)


@pytest.mark.parametrize('login', ['https://github.com/user', 'a@b.com', '@', '@actual-user', 'two users', '-user', 'a_b'])
def test_assignee_rejects_non_accounts(worker, login):
    with pytest.raises(ValueError, match='GitHub 指派账号'):
        replace(worker, assignment_mode='assignee', assignment_value=login).validate()


def test_assignee_query_uses_configured_account_not_logged_in_user(worker):
    calls = []
    def run(args, **kwargs):
        calls.append(args)
        if 'api' in args:
            return SimpleNamespace(returncode=0, stderr='', stdout='[[]]')
        return SimpleNamespace(returncode=0, stderr='', stdout=json.dumps([{
            'number': 7, 'url': 'https://github.com/owner/repo/issues/7',
            'updatedAt': '2026-10-03T00:00:00Z', 'assignees': [{'login': 'agent-account'}],
        }]))
    worker = replace(worker, assignment_mode='assignee', assignment_value='agent-account')
    results = GitHubService('gh', run).list_assigned_issues(worker)
    assert calls[0][calls[0].index('--assignee') + 1] == 'agent-account'
    assert results[0].matches(worker)
    assert len(calls) == 2


def test_assignee_delivery_rechecks_assignment_and_deduplicates(core, worker, issue):
    worker = replace(worker, assignment_mode='assignee', assignment_value='agent-account')
    worker.validate()
    core.db.save_worker(worker)
    assert Worker.from_dict(worker.to_dict()).assignment_value == 'agent-account'
    assigned = replace(issue, assignees=['agent-account'])
    core.github.list_assigned_issues.return_value = [assigned]
    core.github.get_issue.return_value = replace(assigned, assignees=['someone-else'])
    identifier = core.service.discover(worker)[0]
    assert core.service.process_record(identifier) == 'ignored'
    core.codex.send_task.assert_not_called()
    second = replace(assigned, number=2, url='https://github.com/owner/repo/issues/2')
    core.github.list_assigned_issues.return_value = [second]
    core.github.get_issue.return_value = second
    identifier = core.service.discover(worker)[0]
    assert core.service.process_record(identifier) == 'notified'
    assert core.service.discover(worker) == []
    assert core.codex.send_task.call_count == 1


def test_assigned_comments_paginate_filter_author_and_keep_metadata(worker):
    worker = replace(worker, assignment_mode='assignee', assignment_value='agent-account')
    assigned = {**row(), 'assignees': [{'login': 'agent-account'}]}
    pages = [[{**comment(10, text='new request without any mention'), 'user': {'login': 'human'}}],
             [{**comment(11), 'user': {'login': 'AGENT-ACCOUNT'}},
              {**comment(12), 'user': None}, comment(13, number=99)]]
    def run(args, **kwargs):
        return SimpleNamespace(returncode=0, stderr='', stdout=json.dumps(pages if 'api' in args else [assigned]))
    candidates = GitHubService('gh', run).list_assigned_issues(worker)
    assert [item.notification_key for item in candidates] == ['issue', 'assignee:comment:10', 'assignee:comment:12']
    assert all(item.body == '' for item in candidates)
    assert candidates[1].author_login == 'human'


def test_assignee_comment_delivery_restart_and_self_report(core, worker, issue):
    worker = replace(worker, assignment_mode='assignee', assignment_value='agent-account')
    core.db.save_worker(worker)
    source = replace(issue, assignees=['agent-account'], comment_id=10,
                     notification_key='assignee:comment:10', author_login='human')
    core.github.list_assigned_issues.return_value = [source]
    core.github.get_issue.return_value = source
    identifier = core.service.discover(worker)[0]
    assert core.service.process_record(identifier) == 'notified'
    core.github.get_issue.assert_called_with(worker.repository, issue.number, comment_id=10)
    assert '#issuecomment-10' in core.codex.send_task.call_args.args[1]
    from codex_dispatcher.storage.database import Database
    core.service.db = Database(core.db.path)
    assert core.service.discover(worker) == []
    followup = replace(source, comment_id=11, notification_key='assignee:comment:11')
    core.github.list_assigned_issues.return_value = [source, followup]
    core.github.get_issue.return_value = followup
    assert core.service.process_record(core.service.discover(worker)[0]) == 'notified'
    report = replace(source, comment_id=12, notification_key='assignee:comment:12', author_login='AGENT-ACCOUNT')
    identifier = core.db.reserve(worker, report)
    core.github.get_issue.return_value = report
    assert core.service.process_record(identifier) == 'ignored'
    assert '本人' in core.db.record(identifier)['error']
    assert core.codex.send_task.call_count == 2


@pytest.mark.parametrize('response_kind', ['valid', 'deleted', 'wrong-issue'])
def test_assignee_comment_recheck_rest_author_and_deleted_source(response_kind, worker):
    assigned = {**row(), 'assignees': [{'login': 'agent-account'}]}
    def run(args, **kwargs):
        data = assigned
        if 'api' in args:
            if response_kind == 'deleted':
                return SimpleNamespace(returncode=1, stderr='HTTP 404: Not Found', stdout='')
            data = {**comment(10, number=2 if response_kind == 'wrong-issue' else 1), 'user': {'login': 'human'}}
        return SimpleNamespace(returncode=0, stderr='', stdout=json.dumps(data))
    source = GitHubService('gh', run).get_issue('owner/repo', 1, comment_id=10)
    worker = replace(worker, assignment_mode='assignee', assignment_value='agent-account')
    assert source.matches(worker) is (response_kind == 'valid')
    assert source.body == ''
    if response_kind == 'valid':
        assert source.author_login == 'human'
