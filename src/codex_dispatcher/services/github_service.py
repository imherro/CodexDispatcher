from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import webbrowser

from codex_dispatcher.domain.models import DispatchError, Issue, Worker, normalize_repository
from .security import redact


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

    def _run(self, args, *, parse=True):
        executable = self.executable or resolve_gh()
        env = os.environ.copy()
        env['GH_PROMPT_DISABLED'] = '1'
        try:
            result = self.runner([executable, *args], capture_output=True, text=True,
                                 encoding='utf-8', errors='replace', timeout=45, env=env,
                                 creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise DispatchError(redact(f'GitHub 查询失败：{exc}')) from exc
        if result.returncode:
            error = redact(result.stderr or result.stdout)
            if 'rate limit' in error.casefold():
                raise DispatchError('GitHub rate limit：本轮已停止，请稍后重试。' + error[:400])
            raise DispatchError('GitHub CLI：' + error[:800])
        if not parse:
            return True
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise DispatchError('GitHub CLI 返回的 JSON 无效') from exc

    def check_auth(self):
        return self._run(['auth', 'status', '--hostname', 'github.com'], parse=False)

    def test_repository(self, repository):
        repository = normalize_repository(repository)
        self.check_auth()
        repo = self._run(['repo', 'view', repository, '--json', 'nameWithOwner'])
        self._run(['issue', 'list', '--repo', repository, '--state', 'open', '--limit', '1', '--json', 'number'])
        return repo['nameWithOwner']

    def list_assigned_issues(self, worker: Worker):
        repository = normalize_repository(worker.repository)
        flag = '--label' if worker.assignment_mode == 'label' else '--assignee'
        # gh paginates up to this bound; surface saturation rather than silently losing backlog.
        data = self._run(['issue', 'list', '--repo', repository, '--state', 'open',
                          flag, worker.assignment_value, '--limit', '1000', '--json',
                          'number,title,body,url,updatedAt,labels,assignees,state'])
        if len(data) >= 1000:
            raise DispatchError('候选 Issue 达到 1000 条查询上限，请收窄分配规则后重新检查')
        return sorted((Issue.from_github(repository, row) for row in data), key=lambda issue: issue.number)

    def get_issue(self, repository, number):
        repository = normalize_repository(repository)
        data = self._run(['issue', 'view', str(int(number)), '--repo', repository, '--json',
                          'number,title,body,url,updatedAt,labels,assignees,state'])
        issue = Issue.from_github(repository, data)
        issue.comments = self.get_comments(repository, number)
        return issue

    def get_comments(self, repository, number):
        # REST pagination avoids the GraphQL first-page comments limit.
        pages = self._run(['api', '--paginate', '--slurp',
                           f'repos/{normalize_repository(repository)}/issues/{int(number)}/comments'])
        return [{'author': {'login': item.get('user', {}).get('login', '')}, 'body': item.get('body', '')}
                for page in pages for item in page]

    @staticmethod
    def open_issue_in_browser(repository, number):
        webbrowser.open(f'https://github.com/{normalize_repository(repository)}/issues/{int(number)}')
