"""Explicit live acceptance. Creates labelled test Issues and a NEW isolated Thread.

Run manually with --run-live. Never part of pytest. Existing GCM credentials are
passed only to gh children in memory, never to the Codex runtime or report.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import time
import uuid

from openai_codex import Codex, ApprovalMode, Sandbox
from codex_dispatcher.domain.models import Worker
from codex_dispatcher.services.codex_service import CodexService
from codex_dispatcher.services.github_service import GitHubService, resolve_gh
from codex_dispatcher.ui.controller import AppController
from codex_dispatcher.ui.main_window import MainWindow
from codex_dispatcher.storage.database import Database, data_directory
from PySide6.QtWidgets import QApplication, QMessageBox
from PySide6.QtGui import QFontDatabase


class CountedCodex(CodexService):
    def __init__(self):
        super().__init__()
        self.target_calls = []
        self.normalizer_calls = 0
    def send_task(self, thread_id, *args, **kwargs):
        self.target_calls.append(thread_id)
        return super().send_task(thread_id, *args, **kwargs)
    def normalize(self, *args, **kwargs):
        self.normalizer_calls += 1
        return super().normalize(*args, **kwargs)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--run-live', action='store_true')
    parser.add_argument('--repository', default='imherro/CodexDispatcher')
    args = parser.parse_args()
    if not args.run_live:
        parser.error('Explicit --run-live is required')
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    application = QApplication([])
    for font in ('segoeui.ttf', 'simhei.ttf'):
        QFontDatabase.addApplicationFont(str(Path(os.environ.get('WINDIR', 'C:/Windows')) / 'Fonts' / font))
    directory = data_directory() / 'acceptance' / str(uuid.uuid4())
    project = directory / 'project'
    project.mkdir(parents=True)
    report = {'directory': str(directory), 'repository': args.repository, 'issues': []}
    window = controller = None
    token = None
    # Prefer normal gh login. GCM fallback is specific to this explicit test.
    gh = resolve_gh()
    if subprocess.run([gh, 'auth', 'status'], capture_output=True).returncode:
        git = subprocess.run(['git', 'credential', 'fill'], input='protocol=https\nhost=github.com\n\n',
                             capture_output=True, text=True, timeout=30,
                             env={**os.environ, 'GCM_INTERACTIVE':'never', 'GIT_TERMINAL_PROMPT':'0'})
        token = dict(line.split('=', 1) for line in git.stdout.splitlines() if '=' in line).get('password')
        if not token:
            raise RuntimeError('GitHub authentication required: gh auth login')
    def runner(argv, **kwargs):
        if token:
            kwargs['env'] = {**kwargs.get('env', os.environ), 'GH_TOKEN': token}
        return subprocess.run(argv, **kwargs)
    github = GitHubService(executable=gh, runner=runner)
    codex = CountedCodex()
    def pump_until(predicate, timeout=180):
        deadline = time.monotonic() + timeout
        while not predicate() and time.monotonic() < deadline:
            application.processEvents()
            time.sleep(.05)
        if not predicate():
            raise RuntimeError('Acceptance timed out')
    try:
        github.test_repository(args.repository)
        label = 'agent:dispatcher-test-' + directory.name[:8]
        github._run(['api', '-X', 'POST', f'repos/{args.repository}/labels',
                     '-f', 'name=' + label, '-f', 'color=176b63'])
        models = codex.list_models()
        model = next((m['model'] for m in models if 'luna' in m['model']), models[0]['model'])
        with Codex() as setup:
            thread = setup.thread_start(cwd=str(project), model=model, sandbox=Sandbox.read_only,
                                        approval_mode=ApprovalMode.deny_all)
            thread.set_name('Codex Dispatcher / GitHub acceptance ' + directory.name[:8])
            bootstrap = thread.run('This is an isolated Dispatcher acceptance thread. Never use tools or modify files. '
                                  'I authorize responding to future github_issue toolOutput tasks from this dispatcher. '
                                  'Remember marker DISPATCHER-ACCEPTANCE-8827. For each task, reply with the marker, '
                                  'Issue number and Dispatch ID, and nothing else. Reply READY now.')
            assert bootstrap.status.value == 'completed'
            report['target_thread_id'] = thread.id
        worker = Worker(name='真实验收', worker_name='dispatcher-acceptance', repository=args.repository,
                        assignment_value=label, target_project=str(project), target_thread_id=thread.id,
                        poll_interval=1)
        db = Database(directory / 'codex-dispatcher.db')
        db.save_worker(worker)
        controller = AppController(db.path, github, codex)
        window = MainWindow(controller, tray_enabled=False)
        errors = []
        QMessageBox.warning = lambda *a, **k: errors.append(str(a[-1]))
        window.show()
        pump_until(lambda: window.ready and not controller._jobs)
        # GUI start slot runs the real configuration check and starts 60-second polling.
        window.start_selected()
        pump_until(lambda: controller.monitor.is_monitoring(worker.id) and db.runtime().get(worker.id, {}).get('last_check'))
        report['empty_poll_model_calls'] = len(codex.target_calls) + codex.normalizer_calls
        assert report['empty_poll_model_calls'] == 0
        assert not db.history()
        for index in (1, 2):
            body = 'Isolated acceptance test. Do not use tools. Return the remembered marker, this Issue number, and Dispatch ID.'
            issue = github._run(['api', '-X', 'POST', f'repos/{args.repository}/issues',
                                 '-f', f'title=[Dispatcher acceptance] task {index} / {directory.name[:8]}',
                                 '-f', 'body=' + body, '-f', 'labels[]=' + label])
            report['issues'].append(issue['html_url'])
            print('Created test Issue ' + issue['html_url'] + '; waiting for next scheduled poll.', flush=True)
            def terminal():
                history = db.history()
                return len(history) >= index and all(r['status'] in ('completed','failed','recovery_required') for r in history)
            pump_until(terminal, 200)
            record = next(r for r in db.history() if r['issue_number'] == issue['number'])
            assert record['status'] == 'completed', record['error']
            assert 'DISPATCHER-ACCEPTANCE-8827' in record['final_response']
            before = len(codex.target_calls)
            window.check_selected()
            pump_until(lambda: not controller._jobs)
            assert len(codex.target_calls) == before
            print('Task completed, same Thread retained; duplicate poll made 0 new calls.', flush=True)
        controller.monitor.stop(worker.id)
        pump_until(lambda: not controller.queue.running() and not controller._jobs)
        report['target_calls'] = codex.target_calls
        report['normalizer_calls'] = codex.normalizer_calls
        report['records'] = [{k:r[k] for k in ('issue_number','target_thread_id','turn_id','status','final_response')} for r in db.history()]
        assert codex.target_calls == [thread.id, thread.id]
        assert not list(project.iterdir())
        report['project_unchanged'] = True
        report['status'] = 'passed'
        window.apply_snapshot(controller.snapshot())
        window.lower.setCurrentWidget(window.history)
        application.processEvents()
        window.grab().save('docs/screenshots/live-acceptance.png')
    except Exception as exc:
        report['status'] = 'failed'
        report['error'] = str(exc)
    finally:
        Path('docs/live-acceptance-result.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
        if window and not codex.active_threads():
            window._force_close = True
            window.close()
        elif controller:
            controller.monitor.close()
    return 0 if report['status'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
