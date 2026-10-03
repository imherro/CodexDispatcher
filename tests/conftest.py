from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from codex_dispatcher.domain.models import Issue, Worker
from codex_dispatcher.storage.database import Database
from codex_dispatcher.services.codex_service import RunResult
from codex_dispatcher.services.dispatch_service import DispatchService


@pytest.fixture
def worker(tmp_path):
    return Worker(name='Test / worker-1', repository='owner/repo', assignment_mode='label',
                  assignment_value='agent:worker-1', target_project=str(tmp_path), target_thread_id='existing-thread-ABC')


@pytest.fixture
def issue():
    return Issue('owner/repo', 1, 'Test issue', 'Fix it', 'https://github.com/owner/repo/issues/1',
                 '2026-10-02T12:00:00Z', labels=['agent:worker-1'])


@pytest.fixture
def core(tmp_path, worker, issue):
    db = Database(tmp_path / 'test.db')
    db.save_worker(worker)
    github = Mock()
    github.list_assigned_issues.return_value = [issue]
    github.get_issue.return_value = issue
    codex = Mock()
    codex.active_threads.return_value = []
    codex.read_thread.return_value = {'id': worker.target_thread_id, 'cwd': worker.target_project}
    def send(thread_id, task, project, **kwargs):
        kwargs['on_started']('turn-123')
        return RunResult('turn-123', 'completed', 'done')
    codex.send_task.side_effect = send
    return SimpleNamespace(db=db, github=github, codex=codex,
                           service=DispatchService(db, github, codex))
