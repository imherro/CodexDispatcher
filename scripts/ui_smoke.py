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
        worker = Worker(name='示例项目 / codex-worker-1', worker_name='codex-worker-1', repository='example/project',
                        assignment_value='agent:codex-worker-1', target_project=directory,
                        target_thread_id='019example-long-lived-thread')
        db.save_worker(worker)
        github, codex = Mock(), Mock()
        codex.active_threads.return_value = []
        controller = AppController(db.path, github, codex)
        window = MainWindow(controller, tray_enabled=False)
        window.show()
        deadline = time.monotonic() + 10
        while (not window.ready or controller._jobs) and time.monotonic() < deadline:
            application.processEvents()
            time.sleep(.02)
        assert window.ready
        controller.emit({'kind':'monitoring','worker_id':worker.id,'text':'Monitoring · 检查间隔 5 分钟'})
        controller.emit({'kind':'completed','worker_id':worker.id,'text':'Issue #12 completed · 再次轮询不会重复派送'})
        application.processEvents()
        output = Path('docs/screenshots')
        output.mkdir(parents=True, exist_ok=True)
        assert window.grab().save(str(output / 'main-window.png'))
        window.editor.tabs.setCurrentIndex(1)
        application.processEvents()
        assert window.grab().save(str(output / 'advanced-settings.png'))
        window._force_close = True
        window.close()
        print('GUI rendered successfully with fake services; 0 live Codex calls.')


if __name__ == '__main__':
    main()
