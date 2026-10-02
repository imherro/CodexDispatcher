import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from pathlib import Path
from types import SimpleNamespace
import time
from unittest.mock import Mock

import pytest
from PySide6.QtWidgets import QApplication, QMessageBox

from codex_dispatcher.ui.controller import AppController
from codex_dispatcher.ui.main_window import MainWindow
from codex_dispatcher.ui.thread_picker import ThreadPicker
from codex_dispatcher.domain.models import ThreadInfo


@pytest.fixture(scope='module')
def app():
    return QApplication.instance() or QApplication([])


def wait_until(app, predicate, timeout=5):
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.01)
    assert predicate()


def test_gui_worker_save_background_and_visible_output(app, tmp_path, worker, monkeypatch):
    monkeypatch.setattr(QMessageBox, 'warning', lambda *a, **k: pytest.fail('unexpected UI error: ' + str(a[-1])))
    github, codex = Mock(), Mock()
    codex.active_threads.return_value = []
    codex.validate_thread.return_value = ThreadInfo(worker.target_thread_id, worker.target_project)
    controller = AppController(tmp_path / 'gui.db', github=github, codex=codex)
    window = MainWindow(controller, tray_enabled=False)
    window.show()
    wait_until(app, lambda: window.ready)
    window.editor.load(worker)
    window.save_worker()
    wait_until(app, lambda: window._selected_id == worker.id)
    wait_until(app, lambda: len(window.workers) == 1)
    assert window.workers[0].target_thread_id == worker.target_thread_id
    controller.emit({'kind':'message', 'text':'已收到', 'record_id':'record'})
    controller.emit({'kind':'tool', 'text':'commandExecution / completed'})
    app.processEvents()
    assert '已收到' in window.output.toPlainText()
    assert 'commandExecution' in window.output.toPlainText()
    codex.send_task.assert_not_called()
    wait_until(app, lambda: not controller._jobs)
    window._force_close = True
    window.close()


def test_thread_picker_search_and_exact_selection(app, tmp_path):
    threads = [ThreadInfo('thread-A', str(tmp_path), name='主开发', model='model-A'),
               ThreadInfo('thread-B', str(tmp_path), name='Bug 修复')]
    picker = ThreadPicker(threads)
    picker.filter('Bug')
    assert picker.table.isRowHidden(0)
    assert not picker.table.isRowHidden(1)
    picker.table.selectRow(1)
    picker.choose()
    assert picker.selected_id == 'thread-B'
