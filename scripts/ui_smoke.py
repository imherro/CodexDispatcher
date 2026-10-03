"""Render the real GUI with fake services and an isolated database; zero model calls."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from pathlib import Path
import tempfile
import time
import json
from unittest.mock import Mock

from PySide6.QtWidgets import QApplication, QPlainTextEdit
from PySide6.QtGui import QFontDatabase, QPalette, QColor
from PySide6.QtCore import QTimer

from codex_dispatcher.domain.models import Worker, DispatchError
from codex_dispatcher.storage.database import Database
from codex_dispatcher.ui.controller import AppController
from codex_dispatcher.ui.main_window import MainWindow
from codex_dispatcher.ui.history_view import text_dialog
from codex_dispatcher import __version__


def main():
    application = QApplication([])
    dark = QPalette(application.palette())
    dark.setColor(QPalette.Base, QColor('#101010'))
    dark.setColor(QPalette.Text, QColor('#f0f0f0'))
    application.setPalette(dark)
    # Qt's offscreen Windows plugin doesn't discover system fonts automatically.
    for font in ('segoeui.ttf', 'segoeuib.ttf', 'simhei.ttf', 'simsun.ttc'):
        QFontDatabase.addApplicationFont(str(Path(os.environ.get('WINDIR', 'C:/Windows')) / 'Fonts' / font))
    with tempfile.TemporaryDirectory(prefix='dispatcher-ui-smoke-') as directory:
        db = Database(Path(directory) / 'test.db')
        worker = Worker(name='示例项目 / codex-worker-1', repository='example/project',
                        assignment_value='codex-1070-rc', target_project=directory,
                        target_thread_id='019example-long-lived-thread', target_thread_name='修电脑项目会话')
        db.save_worker(worker)
        from dataclasses import replace
        db.save_worker(replace(worker, id='worker-two', name='网站 agent', repository='example/website', assignment_value='website-agent', target_thread_id='019example-website-thread', target_thread_name='网站开发会话'))
        github, codex = Mock(), Mock()
        codex.active_threads.return_value = []
        github.list_repositories.return_value = ['example/project','example/website']
        github.list_assigned_issues.return_value = []
        controller = AppController(db.path, github, codex)
        window = MainWindow(controller, tray_enabled=False)
        window.show()
        deadline = time.monotonic() + 10
        while (not window.ready or controller._jobs) and time.monotonic() < deadline:
            application.processEvents()
            time.sleep(.02)
        assert window.ready
        controller.monitor.start(worker)
        controller.emit({'kind':'notified','worker_id':worker.id,'text':'Issue #12 已通知目标会话'})
        application.processEvents()
        window.apply_snapshot(controller.snapshot())
        output = Path('docs/screenshots')
        output.mkdir(parents=True, exist_ok=True)
        assert window.grab().save(str(output / 'main-window.png'))
        window.edit_worker('worker-two')
        while controller._jobs:
            application.processEvents()
            time.sleep(.01)
        application.processEvents()
        assert window.editor_dialog.grab().save(str(output / 'worker-config.png'))
        window.editor_dialog.reject()
        github.list_repositories.side_effect = DispatchError('GitHub CLI：Get https://api.github.com/user/repos: EOF')
        window.edit_worker('worker-two')
        while controller._jobs:
            application.processEvents()
            time.sleep(.01)
        assert window.editor.isVisible() and window.editor.fields['name'].isVisible()
        assert window.editor.repository_status.isVisible()
        assert window.editor.collect().repository == 'example/website'
        application.processEvents()
        assert window.editor_dialog.grab().save(str(output / 'worker-refresh-error.png'))
        window.editor_dialog.reject()
        github.list_repositories.side_effect = None
        window.edit_worker('worker-two')
        while controller._jobs:
            application.processEvents()
            time.sleep(.01)
        assert window.editor.isVisible() and not window.editor.repository_status.isVisible()
        window.editor_dialog.reject()
        full_text = ('[Codex Dispatcher · example-notification]\n'
                     '你是 @website-agent，请检查 GitHub issues。\n'
                     '有分配给你的待办：https://github.com/example/website/issues/3\n'
                     '请自行读取该 Issue，按本会话已有的项目规范执行、验证并完成收尾，及时汇报进度。')
        failures, observed = [], []
        for _ in range(2):
            def inspect_history():
                dialog = application.activeModalWidget()
                try:
                    assert window.history.isVisible()
                    def inspect_detail():
                        popup = application.activeModalWidget()
                        try:
                            editor = popup.findChild(QPlainTextEdit)
                            assert editor.toPlainText() == full_text
                            assert editor.palette().color(QPalette.Base).lightness() > 200
                            assert editor.palette().color(QPalette.Text).lightness() < 100
                            assert popup.grab().save(str(output / 'notification-detail.png'))
                            observed.append(True)
                        except Exception as error:
                            failures.append(str(error))
                        finally:
                            popup.accept()
                    QTimer.singleShot(50, inspect_detail)
                    text_dialog(window.history, '通知详情', full_text)
                except Exception as error:
                    failures.append(str(error))
                finally:
                    dialog.accept()
            QTimer.singleShot(50, inspect_history)
            window.show_history()
        assert not failures and len(observed) == 2, failures
        codex.send_task.assert_not_called()
        controller.monitor.stop(worker.id)
        while controller._jobs:
            application.processEvents()
            time.sleep(.01)
        window._force_close = True
        window.close()
        report = {'version': __version__, 'status': 'passed', 'dark_system_palette': True,
                  'editor_openings': 3, 'repository_eof_preserves_configuration': True,
                  'repository_refresh_recovery': True, 'history_and_details_openings': 2,
                  'complete_notification_readable': True, 'model_calls': 0}
        Path('docs/gui-acceptance-result.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
        print('GUI rendered successfully with fake services; 0 live Codex calls.')


if __name__ == '__main__':
    main()
