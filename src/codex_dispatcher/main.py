from __future__ import annotations

import argparse
from logging.handlers import RotatingFileHandler
import logging
import os
import subprocess
import sys
import json
from pathlib import Path
import time

from PySide6.QtCore import QLockFile, QTimer
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import QApplication, QMessageBox

from codex_dispatcher.storage.database import data_directory
from codex_dispatcher.services.security import RedactingFormatter, redact
from codex_dispatcher.ui.controller import AppController
from codex_dispatcher.ui.main_window import MainWindow


def hide_child_consoles():
    if os.name != 'nt':
        return
    # SDK starts console binaries without Windows startup flags. Keep GUI child
    # processes hidden while retaining its official process / RPC implementation.
    original = subprocess.Popen
    class HiddenPopen(original):
        def __init__(self, *args, **kwargs):
            kwargs['creationflags'] = kwargs.get('creationflags', 0) | subprocess.CREATE_NO_WINDOW
            super().__init__(*args, **kwargs)
    subprocess.Popen = HiddenPopen


def main():
    parser = argparse.ArgumentParser(description='Codex Dispatcher')
    parser.add_argument('--data-dir', help='Use a separate data directory (development / tests)')
    parser.add_argument('--no-tray', action='store_true')
    parser.add_argument('--smoke-test-output', help='Read-only packaged GUI/runtime smoke test; writes JSON and exits')
    args = parser.parse_args()
    application = QApplication(sys.argv[:1])
    application.setApplicationName('Codex Dispatcher')
    application.setOrganizationName('CodexDispatcher')
    if args.smoke_test_output and os.environ.get('QT_QPA_PLATFORM') == 'offscreen':
        for font in ('segoeui.ttf', 'simhei.ttf'):
            QFontDatabase.addApplicationFont(str(Path(os.environ.get('WINDIR', 'C:/Windows')) / 'Fonts' / font))
    directory = data_directory()
    if args.data_dir:
        directory = Path(args.data_dir).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    lock = QLockFile(str(directory / 'dispatcher.lock'))
    lock.setStaleLockTime(0)
    if not lock.tryLock(100):
        QMessageBox.information(None, 'Codex Dispatcher', '同一数据目录已有 Dispatcher 正在运行，请从系统托盘打开。')
        return 1
    logs = directory / 'logs'
    logs.mkdir(exist_ok=True)
    handler = RotatingFileHandler(logs / 'dispatcher.log', maxBytes=2_000_000, backupCount=5, encoding='utf-8')
    handler.setFormatter(RedactingFormatter('%(asctime)s %(levelname)s %(message)s'))
    logger = logging.getLogger('codex_dispatcher')
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    hide_child_consoles()
    controller = AppController(directory / 'codex-dispatcher.db')
    window = MainWindow(controller, tray_enabled=not args.no_tray)
    def exception_hook(kind, value, traceback):
        logger.error('应用异常：%s', redact(value))
        QMessageBox.critical(window, '应用异常', redact(value))
    sys.excepthook = exception_hook
    window.show()
    if args.smoke_test_output:
        started = time.monotonic()
        smoke_timer = QTimer(window)
        def save_smoke(value=None, error=None):
            value = value or {'status': 'failed', 'error': redact(error)}
            output = Path(args.smoke_test_output).resolve()
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
            window.grab().save(str(output.with_suffix('.png')))
            window._force_close = True
            window.close()
        def smoke_step():
            if window.ready and not controller._jobs:
                smoke_timer.stop()
                def check():
                    from codex_cli_bin import bundled_codex_path
                    binary = bundled_codex_path()
                    assert binary and binary.is_file()
                    runtime = subprocess.run([str(binary), '--version'], capture_output=True, text=True, timeout=20)
                    assert runtime.returncode == 0
                    controller.codex.check_connection()
                    models = controller.codex.list_models()
                    return {'status':'passed', 'frozen':bool(getattr(sys, 'frozen', False)),
                            'gui_initialized':True, 'database_initialized':True, 'runtime':runtime.stdout.strip(),
                            'authenticated':True, 'model_count':len(models), 'model_calls':0}
                window.run_job('packaged-smoke', check, lambda value: save_smoke(value),
                               on_error=lambda error: save_smoke(error=error))
            elif time.monotonic() - started > 120:
                smoke_timer.stop()
                save_smoke(error='GUI initialization timed out')
        smoke_timer.timeout.connect(smoke_step)
        smoke_timer.start(100)
    result = application.exec()
    lock.unlock()
    handler.close()
    return result


if __name__ == '__main__':
    raise SystemExit(main())
