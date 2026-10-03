from dataclasses import replace
import json
import sqlite3
import threading
from types import SimpleNamespace
import pytest

from codex_dispatcher.domain.models import Worker, contains_mention, is_agent_report
from codex_dispatcher.services.github_service import GitHubService
from codex_dispatcher.services.monitor_service import MonitorService
from codex_dispatcher.services.notification_service import build_notification
from codex_dispatcher.storage.database import Database


@pytest.mark.parametrize('text, expected', [
    ('@codex-1070-rc 汇报你的ip地址', True), ('请@CODEX-1070-RC，汇报', True),
    ('(@codex-1070-rc)', True), ('@codex-1070-rc-other', False),
    ('@codex-1070-r', False), ('email@codex-1070-rc', False),
    ('https://github.com/@codex-1070-rc', False), ('@@codex-1070-rc', False),
    ('@codex-1070-rc_name', False), ('@someone-else', False),
    ('codex-1070-rc 检查 PR', True), ('codex-1070-rc检查 PR', True),
    ('CODEX-1070-RC', True), ('codex-1070-rc-other', False),
    ('codex-1070-rc@example.com', False), ('prefixcodex-1070-rc', False),
])
def test_exact_mention_boundary(text, expected):
    assert contains_mention(text, '@codex-1070-rc') is expected


def test_default_and_optional_at_prefix(worker):
    assert Worker().assignment_mode == 'mention'
    worker.assignment_mode = 'mention'
    worker.assignment_value = ' @codex-1070-rc '
    worker.validate()
    assert worker.assignment_value == 'codex-1070-rc'
    worker.assignment_value = 'two names'
    with pytest.raises(ValueError, match='@ 名称'):
        worker.validate()


def row(number=1, body='', title=''):
    return {'number':number,'title':title,'body':body,'url':f'https://github.com/owner/repo/issues/{number}',
            'updatedAt':'2026-10-03T00:00:00Z','labels':[],'assignees':[],'state':'OPEN'}


def comment(identifier, number=1, text='@codex-1070-rc 汇报你的ip地址'):
    return {'id':identifier, 'body':text, 'issue_url':f'https://api.github.com/repos/owner/repo/issues/{number}'}


def service(rows, pages):
    calls = []
    def run(args, **kwargs):
        calls.append(args)
        return SimpleNamespace(returncode=0, stderr='', stdout=json.dumps(pages if 'api' in args else rows))
    return GitHubService('gh', run), calls


def test_issue_title_body_and_paginated_comments_match_independently(worker):
    worker = replace(worker, assignment_mode='mention', assignment_value='codex-1070-rc')
    github, calls = service([row(body='@codex-1070-rc 原始任务'), row(2, title='@codex-1070-rc 标题任务')],
                            [[comment(10),comment(11,text='@other-agent')], [comment(12),comment(13,number=99)]])
    candidates = github.list_assigned_issues(worker)
    assert [(i.number,i.notification_key) for i in candidates] == [
        (1,'mention:issue'),(1,'mention:comment:10'),(1,'mention:comment:12'),(2,'mention:issue')]
    assert len(calls) == 2
    assert '--label' not in calls[0] and '--assignee' not in calls[0]
    assert '--paginate' in calls[1] and '--slurp' in calls[1]
    assert all('body' not in i.metadata() and 'title' not in i.metadata() for i in candidates)


def test_empty_repository_does_not_query_comments(worker):
    github, calls = service([], [])
    assert github.list_assigned_issues(replace(worker, assignment_mode='mention')) == []
    assert len(calls) == 1


def test_reply_matches_without_at_even_when_title_names_another_agent(worker):
    github, _ = service([row(4, title='codex-1070-1 检查 PR')], [[
        comment(10, number=4, text='codex-1070-rc检查你提交的pr是否合并'),
        comment(11, number=4, text='codex-1070-rc 检查你提交的pr是否合并')]])
    worker = replace(worker, assignment_mode='mention', assignment_value='codex-1070-rc')
    assert [issue.comment_id for issue in github.list_assigned_issues(worker)] == [10, 11]


@pytest.mark.parametrize('text,expected', [
    ('我是 @codex-1070-rc , 完成任务情况如下：请补充城市。', True),
    ('我是 codex-1070-rc。已完成北京天气查询。', True),
    ('我是 **codex-1070-rc**，已核实。', True),
    ("I am @codex-1070-rc. Task complete.", True),
    ('@codex-1070-rc 我要北京的天气', False),
    ('codex-1070-rc检查 PR', False),
    ('我是用户，请 codex-1070-rc 继续处理', False),
    ('我是 codex-1070-rc-other，请 @codex-1070-rc 协助', False),
])
def test_only_explicit_target_self_signature_is_a_report(text, expected):
    assert is_agent_report(text, 'codex-1070-rc') is expected


def test_signed_reports_do_not_trigger_but_later_user_request_does(worker):
    worker = replace(worker, assignment_mode='mention', assignment_value='codex-1070-rc')
    github, _ = service([row(26, title='codex-1070-rc 汇报天气')], [[
        comment(10, 26, '我是 @codex-1070-rc , 请补充城市。'),
        comment(11, 26, '@codex-1070-rc 我要北京的天气'),
        comment(12, 26, '我是 codex-1070-rc , 已查询北京天气。'),
        comment(13, 26, 'codex-1070-rc 再查上海的天气'),
    ]])
    candidates = github.list_assigned_issues(worker)
    assert [issue.notification_key for issue in candidates] == ['mention:issue', 'mention:comment:11', 'mention:comment:13']


def test_old_queued_report_is_skipped_before_sending(core, worker):
    worker = replace(worker, assignment_mode='mention', assignment_value='codex-1070-rc')
    issue = replace(core.github.get_issue.return_value, title='', body='我是 @codex-1070-rc , 已完成。',
                    comment_id=12, notification_key='mention:comment:12')
    core.db.save_worker(worker)
    identifier = core.db.reserve(worker, issue)
    core.github.get_issue.return_value = issue
    assert core.service.process_record(identifier) == 'ignored'
    assert '署名汇报' in core.db.record(identifier)['error']
    core.codex.send_task.assert_not_called()


def test_comments_exclude_closed_issues_and_ignored_labels(worker):
    ignored = {**row(), 'labels':[{'name':'agent:running'}]}
    github, _ = service([ignored], [[comment(10), comment(11,number=2)]])
    assert github.list_assigned_issues(replace(worker, assignment_mode='mention', assignment_value='codex-1070-rc')) == []


def test_new_comment_on_notified_issue_survives_restart_and_sends_only_anchor(core, worker, issue):
    worker = replace(worker, assignment_mode='mention', assignment_value='codex-1070-rc')
    core.db.save_worker(worker)
    first = replace(issue, title='', body='@codex-1070-rc 汇报你的ip地址 PRIVATE',
                    notification_key='mention:comment:123', comment_id=123)
    core.github.list_assigned_issues.return_value = [first]
    core.github.get_issue.return_value = first
    identifier = core.service.discover(worker)[0]
    assert core.service.process_record(identifier) == 'notified'
    message = core.codex.send_task.call_args.args[1]
    assert 'https://github.com/owner/repo/issues/1#issuecomment-123' in message
    assert '汇报你的ip地址' not in message and 'PRIVATE' not in message
    core.github.get_issue.assert_called_with(worker.repository, 1, mention=True, comment_id=123)
    core.service.db = Database(core.db.path)
    assert core.service.discover(worker) == []
    second = replace(first, notification_key='mention:comment:124', comment_id=124)
    core.github.list_assigned_issues.return_value = [first, second]
    core.github.get_issue.return_value = second
    identifier2 = core.service.discover(worker)[0]
    assert identifier2 != identifier
    assert core.service.process_record(identifier2) == 'notified'
    assert core.codex.send_task.call_count == 2
    assert core.service.discover(worker) == []


def test_recheck_removal_skips_mention(core, worker, issue):
    worker = replace(worker, assignment_mode='mention', assignment_value='codex-1070-rc')
    source = replace(issue, body='@codex-1070-rc hi', notification_key='mention:issue')
    core.github.list_assigned_issues.return_value = [source]
    identifier = core.service.discover(worker)[0]
    core.github.get_issue.return_value = replace(source, title='', body='removed')
    assert core.service.process_record(identifier) == 'ignored'
    core.codex.send_task.assert_not_called()


def test_deleted_or_wrong_issue_comment_cannot_fall_back_to_body():
    for response in (SimpleNamespace(returncode=1,stdout='',stderr='HTTP 404: Not Found'),
                     SimpleNamespace(returncode=0,stdout=json.dumps(comment(12,number=99)),stderr='')):
        def run(args, **kwargs):
            if 'api' in args:
                return response
            return SimpleNamespace(returncode=0, stderr='', stdout=json.dumps(row(body='@codex-1070-rc')))
        result = GitHubService('gh', run).get_issue('owner/repo', 1, mention=True, comment_id=12)
        assert result.body == result.title == '' and result.comment_id == 12


def test_repository_picker_pages_include_collaborator_and_organization():
    pages = [[{'full_name':'owner/repo'}], [{'full_name':'org/project'}, {'full_name':'owner/repo'}]]
    github, calls = service([], pages)
    assert github.list_repositories() == ['org/project', 'owner/repo']
    assert 'collaborator' in calls[0][-1] and 'organization_member' in calls[0][-1]


def test_migrate_v1_database_preserves_config_and_dedup(core, worker):
    identifier = core.service.discover(worker)[0]
    core.service.process_record(identifier)
    with sqlite3.connect(core.db.path) as db:
        db.execute('DROP INDEX records_notification')
        db.execute('ALTER TABLE dispatch_records DROP COLUMN notification_key')
        db.execute('PRAGMA user_version=1')
    migrated = Database(core.db.path)
    assert migrated.record(identifier)['notification_key'] == 'issue'
    assert migrated.get_worker(worker.id).assignment_mode == 'label'
    assert not migrated.eligible(worker, core.github.get_issue.return_value)
    assert Database(core.db.path).record(identifier)['turn_id'] == 'turn-123'


def test_monitor_countdown_tracks_poll_and_stop_without_models(core, worker, monkeypatch):
    clock = [100.0]
    monkeypatch.setattr('codex_dispatcher.services.monitor_service.time.monotonic', lambda: clock[0])
    checked = threading.Event()
    core.github.list_assigned_issues.side_effect = lambda w: checked.set() or []
    queue = SimpleNamespace(allow=lambda *args: None, pause=lambda *args: None, close=lambda: None)
    monitor = MonitorService(core.service, queue)
    monitor.start(worker)
    assert checked.wait(3)
    # Wait for the check's metadata write and next-check scheduling without sleeping minutes.
    import time
    for _ in range(100):
        if monitor.countdown(worker.id) == 300:
            break
        threading.Event().wait(.01)
    assert monitor.countdown(worker.id) == 300
    clock[0] += 7
    assert monitor.countdown(worker.id) == 293
    monitor.stop(worker.id)
    assert monitor.countdown(worker.id) is None
    assert not core.codex.mock_calls
    monitor.close()


def test_comment_locator_rejects_untrusted_identifiers():
    with pytest.raises(ValueError):
        build_notification('owner/repo', 1, '4dff84b8-bd9f-438e-9550-b7a51b720c06', comment_id='123#attack')
