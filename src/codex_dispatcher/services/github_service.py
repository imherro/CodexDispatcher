from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import re
import time
import webbrowser
from dataclasses import replace
from urllib.parse import urlparse

from codex_dispatcher.domain.models import DispatchError, Issue, Worker, normalize_repository, normalize_github_login
from .security import redact


class GitHubUnavailable(DispatchError):
    """A read-only query failed temporarily; a queued notice can safely wait."""


def resolve_gh() -> str:
    executable = shutil.which('gh')
    if executable:
        return executable
    for root in (os.environ.get('ProgramFiles', ''), os.environ.get('LOCALAPPDATA', '')):
        candidate = Path(root) / 'GitHub CLI' / 'gh.exe'
        if candidate.is_file():
            return str(candidate)
    raise DispatchError('未找到 GitHub CLI，请安装 gh 并运行 gh auth login')


class GitHubService:
    normalize_repository = staticmethod(normalize_repository)

    def __init__(self, executable=None, runner=subprocess.run):
        self.executable = executable
        self.runner = runner

    def _query(self, args, *, parse=True):
        return self._run(args, parse=parse, retry=True)

    def _run(self, args, *, parse=True, retry=False):
        executable = self.executable or resolve_gh()
        env = os.environ.copy()
        env['GH_PROMPT_DISABLED'] = '1'
        deadline = time.monotonic() + 45
        for attempt in range(3 if retry else 1):
            try:
                result = self.runner([executable, *args], capture_output=True, text=True,
                                     encoding='utf-8', errors='replace', timeout=min(45, max(.1, deadline - time.monotonic())), env=env,
                                     creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
            except subprocess.TimeoutExpired:
                error = 'GitHub 请求超时'
                transient = True
            except OSError as exc:
                raise DispatchError(redact(f'GitHub 查询失败：{exc}')) from exc
            else:
                if not result.returncode:
                    break
                error = redact(result.stderr or result.stdout)
                transient = bool(re.search(r'\beof\b|connection reset|broken pipe|timed out|timeout|'
                                           r'temporary failure|http (502|503|504)\b', error, re.IGNORECASE))
            if 'rate limit' in error.casefold():
                raise DispatchError('GitHub rate limit：本轮已停止，请稍后重试。' + error[:400])
            if not retry or not transient:
                raise DispatchError('GitHub CLI：' + error[:800])
            delay = .5 * (attempt + 1)
            if attempt == 2 or time.monotonic() + delay >= deadline:
                raise GitHubUnavailable('GitHub 暂时连接失败，已自动重试；请稍后再试。' + error[:400])
            time.sleep(delay)
        if not parse:
            return True
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise DispatchError('GitHub CLI 返回的 JSON 无效') from exc

    def check_auth(self):
        return self._query(['auth', 'status', '--hostname', 'github.com'], parse=False)

    def test_repository(self, repository):
        repository = normalize_repository(repository)
        self.check_auth()
        repo = self._query(['repo', 'view', repository, '--json', 'nameWithOwner'])
        self._query(['issue', 'list', '--repo', repository, '--state', 'open', '--limit', '1', '--json', 'number'])
        return repo['nameWithOwner']

    def list_repositories(self):
        pages = self._query(['api', '--paginate', '--slurp',
                          'user/repos?per_page=100&sort=updated&affiliation=owner,collaborator,organization_member'])
        return sorted({normalize_repository(row['full_name']) for page in pages for row in page}, key=str.casefold)

    def list_assigned_issues(self, worker: Worker):
        repository = normalize_repository(worker.repository)
        if worker.assignment_mode == 'mention':
            return self._list_mentions(worker, repository)
        flag = '--label' if worker.assignment_mode == 'label' else '--assignee'
        value = normalize_github_login(worker.assignment_value) if worker.assignment_mode == 'assignee' else worker.assignment_value
        # gh paginates up to this bound; surface saturation rather than silently losing backlog.
        data = self._query(['issue', 'list', '--repo', repository, '--state', 'open',
                          flag, value, '--limit', '1000', '--json',
                          'number,url,updatedAt,labels,assignees,state'])
        if len(data) >= 1000:
            raise DispatchError('候选 Issue 达到 1000 条查询上限，请收窄分配规则后重新检查')
        issues = {row['number']: Issue.from_github(repository, row) for row in data}
        result = [issue for issue in issues.values() if issue.matches(worker)]
        if worker.assignment_mode == 'assignee':
            result.extend(self._list_comments(worker, repository, issues))
        return sorted(result, key=lambda issue: (issue.number, issue.comment_id or 0))

    @staticmethod
    def _comment_issue(repository, comment):
        parsed = urlparse(comment.get('issue_url', ''))
        prefix = f'/repos/{repository}/issues/'
        if parsed.scheme != 'https' or parsed.hostname != 'api.github.com' or not parsed.path.casefold().startswith(prefix.casefold()):
            return None
        number = parsed.path[len(prefix):]
        return int(number) if number.isascii() and number.isdigit() else None

    @staticmethod
    def _comment_candidate(issue, comment, *, mode='mention'):
        identifier = comment.get('id')
        if type(identifier) is not int or identifier < 1:
            raise DispatchError('GitHub 返回的评论编号无效')
        return replace(issue, title='', body=(comment.get('body') or '') if mode == 'mention' else '',
                       author_login=(comment.get('user') or {}).get('login') or '',
                       notification_key=f'{mode}:comment:{identifier}', comment_id=identifier)

    def _list_mentions(self, worker, repository):
        # Literal matching supports virtual names that are not GitHub accounts.
        rows = self._query(['issue', 'list', '--repo', repository, '--state', 'open', '--limit', '1000',
                          '--json', 'number,title,body,url,updatedAt,labels,assignees,state'])
        if len(rows) >= 1000:
            raise DispatchError('@ 规则的打开 Issue 达到 1000 条上限，请缩小仓库范围')
        issues = {row['number']: Issue.from_github(repository, row, include_content=True) for row in rows}
        result = []
        for issue in issues.values():
            candidate = replace(issue, notification_key='mention:issue')
            if candidate.matches(worker):
                result.append(candidate)
        result.extend(self._list_comments(worker, repository, issues))
        return sorted(result, key=lambda issue: (issue.number, issue.comment_id or 0))

    def _list_comments(self, worker, repository, issues):
        result = []
        if issues:
            # gh handles every REST page; comments on PRs / closed Issues are excluded.
            pages = self._query(['api', '--paginate', '--slurp',
                              f'repos/{repository}/issues/comments?per_page=100'])
            for page in pages:
                for comment in page:
                    issue = issues.get(self._comment_issue(repository, comment))
                    if issue:
                        candidate = self._comment_candidate(issue, comment, mode=worker.assignment_mode)
                        if candidate.matches(worker):
                            result.append(candidate)
        return result

    def get_issue(self, repository, number, *, mention=False, comment_id=None):
        repository = normalize_repository(repository)
        fields = 'number,url,updatedAt,labels,assignees,state'
        if mention:
            fields += ',title,body'
        data = self._query(['issue', 'view', str(int(number)), '--repo', repository, '--json',
                          fields])
        issue = Issue.from_github(repository, data, include_content=mention)
        if not mention and comment_id is None:
            return issue
        if mention:
            issue.notification_key = 'mention:issue'
        if comment_id is not None:
            if type(comment_id) is not int or comment_id < 1:
                raise ValueError('评论编号无效')
            try:
                comment = self._query(['api', f'repos/{repository}/issues/comments/{comment_id}'])
            except DispatchError as exc:
                if 'HTTP 404' not in str(exc):
                    raise
                # Deleted or inaccessible source must not accidentally fall back to Issue text.
                return replace(issue, title='', body='', state='CLOSED', comment_id=comment_id)
            if self._comment_issue(repository, comment) != int(number):
                return replace(issue, title='', body='', state='CLOSED', comment_id=comment_id)
            issue = self._comment_candidate(issue, comment, mode='mention' if mention else 'assignee')
        return issue

    @staticmethod
    def open_issue_in_browser(repository, number):
        webbrowser.open(f'https://github.com/{normalize_repository(repository)}/issues/{int(number)}')
