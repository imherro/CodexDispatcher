import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from dataclasses import replace
import time
from unittest.mock import Mock
import pytest
from PySide6.QtWidgets import QApplication, QMessageBox
from codex_dispatcher.ui.controller import AppController
from codex_dispatcher.ui.main_window import MainWindow
from codex_dispatcher.ui.thread_picker import ThreadPicker
from codex_dispatcher.domain.models import ThreadInfo
from codex_dispatcher.storage.database import Database


@pytest.fixture(scope='module')
def app():
    return QApplication.instance() or QApplication([])


def wait_until(app, predicate, timeout=5):
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.01)
    assert predicate()


def services(worker):
    github, codex = Mock(), Mock()
    github.list_assigned_issues.return_value = []
    codex.active_threads.return_value = []
    codex.read_thread.return_value = {'id':worker.target_thread_id, 'cwd':worker.target_project}
    return github, codex


def close(app, window):
    wait_until(app, lambda: not window.controller._jobs)
    window._force_close = True
    window.close()


def test_small_editor_save_metadata_only_and_no_stream(app, tmp_path, worker, monkeypatch):
    monkeypatch.setattr(QMessageBox, 'warning', lambda *a, **k: pytest.fail(str(a[-1])))
    github, codex = services(worker)
    window = MainWindow(AppController(tmp_path / 'gui.db', github, codex), tray_enabled=False)
    window.show()
    wait_until(app, lambda: window.ready)
    window.editor.load(worker)
    window.save_worker()
    wait_until(app, lambda: len(window.workers) == 1)
    assert window.workers[0].target_project == worker.target_project
    assert set(window.action_buttons) == {'new', 'edit', 'monitor'}
    assert set(window.editor.fields) == {'name','repository','assignment_mode','assignment_value','target_thread_id','enabled','poll_interval'}
    assert '#16803c' in window.codex_status.text()
    codex.send_task.assert_not_called()
    codex.read_thread.assert_called_once_with(worker.target_thread_id)
    previous = window.statusBar().currentMessage()
    window.receive_event({'kind':'message', 'text':'unwanted agent output'})
    assert window.statusBar().currentMessage() == previous
    close(app, window)


def test_thread_picker_search_and_exact_selection(app, tmp_path):
    threads = [ThreadInfo('thread-A', str(tmp_path), name='主开发'),
               ThreadInfo('thread-B', str(tmp_path), name='Bug 修复')]
    picker = ThreadPicker(threads)
    picker.filter('Bug')
    assert picker.table.isRowHidden(0)
    assert not picker.table.isRowHidden(1)
    picker.table.selectRow(1)
    picker.choose()
    assert picker.selected_id == 'thread-B'


def test_one_button_controls_multiple_workers_without_interrupt(app, tmp_path, worker, monkeypatch):
    monkeypatch.setattr(QMessageBox, 'warning', lambda *a, **k: pytest.fail(str(a[-1])))
    db = Database(tmp_path / 'toggle.db')
    worker2 = replace(worker, id='second-worker', target_thread_id='second-thread')
    db.save_worker(worker)
    db.save_worker(worker2)
    github, codex = services(worker)
    codex.read_thread.side_effect = lambda tid: {'id':tid, 'cwd':worker.target_project}
    controller = AppController(db.path, github, codex)
    window = MainWindow(controller, tray_enabled=False)
    window.show()
    wait_until(app, lambda: window.ready and not controller._jobs)
    button = window.action_buttons['monitor']
    assert button.text() == '开始监测'
    button.click()
    wait_until(app, lambda: button.text() == '停止监测' and button.isEnabled())
    assert all(controller.monitor.is_monitoring(w.id) for w in (worker,worker2))
    codex.active_threads.return_value = ['second-thread']
    button.click()
    wait_until(app, lambda: button.text() == '开始监测' and button.isEnabled())
    assert all(not controller.monitor.is_monitoring(w.id) for w in (worker,worker2))
    codex.send_task.assert_not_called()
    codex.interrupt.assert_not_called()
    codex.active_threads.return_value = []
    close(app, window)
