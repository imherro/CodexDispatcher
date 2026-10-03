"""Render the real GUI with fake services and an isolated database; zero model calls."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from pathlib import Path
import tempfile
import time
from unittest.mock import Mock

from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QFontDatabase

from codex_dispatcher.domain.models import Worker
from codex_dispatcher.storage.database import Database
from codex_dispatcher.ui.controller import AppController
from codex_dispatcher.ui.main_window import MainWindow


def main():
    application = QApplication([])
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
        controller.monitor.stop(worker.id)
        while controller._jobs:
            application.processEvents()
            time.sleep(.01)
        window._force_close = True
        window.close()
        print('GUI rendered successfully with fake services; 0 live Codex calls.')


if __name__ == '__main__':
    main()
