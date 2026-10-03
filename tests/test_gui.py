import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from dataclasses import replace
import time
from unittest.mock import Mock
import pytest
from PySide6.QtGui import QPalette, QColor
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox, QPlainTextEdit
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
    github.list_repositories.return_value = ['owner/repo','other/repository']
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
    template = '{notification_id}\n请处理 {issue_url}，完成后关闭 Issue。'
    window.editor.fields['notification_template'].setPlainText(template)
    assert window.editor.collect().notification_template == template
    assert 'https://github.com/owner/repo/issues/1' in window.editor.preview.toPlainText()
    assert window.editor.dirty()
    window.editor.load(window.editor.collect())
    assert not window.editor.dirty()
    window.editor.fields['notification_template'].setPlainText('{body}')
    assert '格式错误' in window.editor.preview.toPlainText()
    window.editor.fields['notification_template'].setPlainText(template)
    window.save_worker()
    wait_until(app, lambda: len(window.workers) == 1)
    assert window.workers[0].target_project == worker.target_project
    assert window.workers[0].notification_template == template
    assert set(window.action_buttons) == {'new'}
    assert set(window.worker_actions[worker.id]) == {'edit', 'monitor', 'check'}
    assert set(window.editor.fields) == {'name','repository','assignment_mode','assignment_value','target_thread_id','enabled','poll_interval','notification_template'}
    assert '#16803c' in window.codex_status.text()
    codex.send_task.assert_not_called()
    codex.read_thread.assert_called_once_with(worker.target_thread_id)
    github.test_repository.assert_not_called()
    # Existing notification-format edits remain saveable while both services
    # are unavailable. Choosing another target still requires validation.
    from codex_dispatcher.domain.models import DispatchError
    github.test_repository.side_effect = DispatchError('GitHub EOF')
    codex.read_thread.side_effect = DispatchError('workspace routing discovery timed out')
    codex.check_connection.side_effect = DispatchError('offline')
    updated_template = template + '\n及时汇报进度。'
    window.editor.fields['notification_template'].setPlainText(updated_template)
    window.save_worker()
    wait_until(app, lambda: window.workers[0].notification_template == updated_template)
    assert window.controller.db.get_worker(worker.id).notification_template == updated_template
    assert codex.read_thread.call_count == 1
    github.test_repository.assert_not_called()
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


def test_worker_buttons_are_independent_with_manual_check_and_countdown(app, tmp_path, worker, monkeypatch):
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
    button = window.worker_actions[worker.id]['monitor']
    other = window.worker_actions[worker2.id]['monitor']
    assert button.accessibleName() == '开始监测'
    assert button.text() == '' and not button.icon().isNull()
    assert window.worker_table.columnWidth(6) == 118
    button.click()
    wait_until(app, lambda: button.accessibleName() == '停止监测' and button.isEnabled())
    assert controller.monitor.is_monitoring(worker.id)
    assert not controller.monitor.is_monitoring(worker2.id)
    assert other.accessibleName() == '开始监测'
    wait_until(app, lambda: 0 < (controller.monitor.countdown(worker.id) or 0) <= 300)
    window.apply_snapshot(controller.snapshot())
    assert window.worker_table.item(0,4).text() != '—'
    assert window.worker_table.item(1,4).text() == '—'
    before = github.list_assigned_issues.call_count
    window.worker_actions[worker2.id]['check'].click()
    wait_until(app, lambda: 'check:' + worker2.id not in controller._jobs)
    assert github.list_assigned_issues.call_count > before
    assert not controller.monitor.is_monitoring(worker2.id)
    codex.active_threads.return_value = ['second-thread']
    button.click()
    wait_until(app, lambda: button.accessibleName() == '开始监测' and button.isEnabled())
    assert not controller.monitor.is_monitoring(worker.id)
    assert not controller.monitor.is_monitoring(worker2.id)
    assert controller.monitor.countdown(worker.id) is None
    window.worker_actions[worker2.id]['edit'].click()
    wait_until(app, lambda: window.editor_dialog is not None and not controller._jobs)
    assert window.editor.worker.id == worker2.id
    window.editor_dialog.reject()
    codex.send_task.assert_not_called()
    codex.interrupt.assert_not_called()
    codex.active_threads.return_value = []
    close(app, window)


def test_new_worker_defaults_to_mention_and_selectable_repository(app, tmp_path, worker):
    github, codex = services(worker)
    controller = AppController(tmp_path / 'picker.db', github, codex)
    window = MainWindow(controller, tray_enabled=False)
    window.show()
    wait_until(app, lambda: window.ready and not controller._jobs)
    window.new_worker()
    wait_until(app, lambda: not controller._jobs)
    assert window.editor.collect().assignment_mode == 'mention'
    field = window.editor.fields['repository']
    assert not field.isEditable()
    field.setCurrentIndex(field.findData('other/repository'))
    assert window.editor.collect().repository == 'other/repository'
    assert '@codex-1070-rc' in window.editor.fields['assignment_value'].placeholderText()
    window.editor_dialog.reject()
    close(app, window)


def test_reopening_editor_keeps_form_visible_when_repository_refresh_fails(app, tmp_path, worker, monkeypatch):
    from codex_dispatcher.domain.models import DispatchError
    monkeypatch.setattr(QMessageBox, 'warning', lambda *a, **k: pytest.fail('Refresh must not open a blocking dialog'))
    github, codex = services(worker)
    github.list_repositories.side_effect = [['owner/repo'], DispatchError('GitHub CLI：EOF'), ['owner/repo','other/repository']]
    window = MainWindow(AppController(tmp_path / 'reopen.db', github, codex), tray_enabled=False)
    window.show()
    wait_until(app, lambda: window.ready and not window.controller._jobs)
    for opening in range(3):
        window.open_editor(worker)
        wait_until(app, lambda: not window.controller._jobs)
        assert window.editor.isVisible()
        assert window.editor.fields['name'].isVisible()
        assert window.editor.fields['notification_template'].isVisible()
        assert window.editor.fields['name'].text() == worker.name
        assert window.editor.collect().repository == worker.repository
        if opening == 1:
            assert window.editor.repository_status.isVisible()
            assert 'EOF' in window.editor.repository_status.text()
            assert window.editor.repository_refresh.isEnabled()
            window.editor.repository_refresh.click()
            wait_until(app, lambda: not window.controller._jobs)
            assert not window.editor.repository_status.isVisible()
            assert window.editor.fields['repository'].findData('other/repository') >= 0
            github.list_repositories.side_effect = None
        window.editor_dialog.reject()
        assert not window.editor.isVisible()
    codex.send_task.assert_not_called()
    close(app, window)


def test_dark_system_palette_keeps_complete_notification_and_editor_readable(app, tmp_path, worker, monkeypatch):
    from codex_dispatcher.ui.history_view import text_dialog
    previous_palette = app.palette()
    dark = QPalette(previous_palette)
    dark.setColor(QPalette.Base, QColor('#101010'))
    dark.setColor(QPalette.Text, QColor('#f0f0f0'))
    app.setPalette(dark)
    github, codex = services(worker)
    window = MainWindow(AppController(tmp_path / 'contrast.db', github, codex), tray_enabled=False)
    window.show()
    wait_until(app, lambda: window.ready and not window.controller._jobs)
    window.open_editor(worker)
    wait_until(app, lambda: not window.controller._jobs)
    for field in (window.editor.fields['notification_template'], window.editor.preview):
        assert field.palette().color(QPalette.Base).lightness() > 200
        assert field.palette().color(QPalette.Text).lightness() < 100
    window.editor_dialog.reject()
    full_text = '第一行通知\n有分配给你的待办：https://github.com/owner/repo/issues/1\n请自行读取该 Issue。\n执行、验证并完成收尾，及时汇报进度。'
    observed = []
    # Inspect the actual widgets without starting nested application event
    # loops in the shared pytest QApplication. The standalone UI smoke script
    # also exercises real modal loops in its own process.
    def inspect_dialog(dialog):
        dialog.show()
        editor = dialog.findChild(QPlainTextEdit)
        if dialog.windowTitle() == '通知记录':
            observed.append(window.history.isVisible())
            text_dialog(window.history, '通知详情', full_text)
        else:
            observed.append((editor.toPlainText(), editor.palette().color(QPalette.Base).lightness(),
                             editor.palette().color(QPalette.Text).lightness()))
        dialog.accept()
        return QDialog.Accepted
    monkeypatch.setattr(QDialog, 'exec', inspect_dialog)
    for _ in range(2):
        window.show_history()
    assert observed[0] is True and observed[2] is True
    for text, background, foreground in (observed[1], observed[3]):
        assert text == full_text and background > 200 and foreground < 100
    close(app, window)
    app.setPalette(previous_palette)


