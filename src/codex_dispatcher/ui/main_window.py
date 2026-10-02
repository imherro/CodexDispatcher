from __future__ import annotations

from datetime import datetime
import logging
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QAction, QDesktopServices, QIcon, QPainter, QPixmap, QColor
from PySide6.QtWidgets import (QApplication, QCheckBox, QDialog, QDialogButtonBox, QHBoxLayout,
                              QLabel, QListWidget, QListWidgetItem, QMainWindow, QMenu,
                              QMessageBox, QPlainTextEdit, QPushButton, QSplitter,
                              QStyle, QSystemTrayIcon, QTabWidget, QVBoxLayout, QWidget)

from codex_dispatcher.domain.models import Worker
from codex_dispatcher.runtime.locks import ThreadLocks
from codex_dispatcher.services.project_service import inspect_project
from codex_dispatcher.services.security import redact
from .dashboard import Dashboard
from .history_view import HistoryView, text_dialog
from .thread_picker import ThreadPicker
from .worker_editor import WorkerEditor


def app_icon():
    pixmap = QPixmap(64, 64)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setBrush(QColor('#176b63'))
    painter.setPen(Qt.NoPen)
    painter.drawRoundedRect(2, 2, 60, 60, 12, 12)
    painter.setPen(Qt.white)
    font = painter.font()
    font.setBold(True)
    font.setPixelSize(38)
    painter.setFont(font)
    painter.drawText(pixmap.rect(), Qt.AlignCenter, 'D')
    painter.end()
    return QIcon(pixmap)


class MainWindow(QMainWindow):
    def __init__(self, controller, *, tray_enabled=True):
        super().__init__()
        self.controller = controller
        self.workers, self.runtime, self.records = [], {}, []
        self.callbacks = {}
        self.ready = False
        self._force_close = False
        self._selected_id = None
        self._stream_prefix = None
        self.setWindowTitle('Codex Dispatcher')
        self.setWindowIcon(app_icon())
        self.resize(1280, 900)
        self.setMinimumSize(1040, 720)
        self.setStyleSheet('''
            QMainWindow { background: #f3f5f7; }
            QWidget { font-family: "Segoe UI", "Microsoft YaHei UI", "SimHei"; font-size: 13px; color: #233043; }
            QPushButton { background: #ffffff; border: 1px solid #ced6df; border-radius: 5px; padding: 7px 12px; }
            QPushButton:hover { background: #eaf1f5; }
            QPushButton:disabled { color: #8895a5; background: #eef1f4; }
            QPushButton#primary { background: #176b63; color: white; border-color: #176b63; }
            QLineEdit, QComboBox, QSpinBox { background: white; border: 1px solid #ced6df; border-radius: 4px; padding: 6px; }
            QPlainTextEdit, QListWidget, QTableWidget { background: white; border: 1px solid #d9e0e8; }
            QTabWidget::pane { background: white; border: 1px solid #d9e0e8; }
            QTabBar::tab { padding: 8px 17px; background: #e8edf2; }
            QTabBar::tab:selected { background: white; color: #176b63; }
            QLabel#title { font-size: 23px; font-weight: 600; }
            QLabel#hint { color: #68768a; }
            QLabel#notice { background: #eef6f4; color: #24574f; padding: 10px; border-radius: 5px; }
            QHeaderView::section { background: #edf1f5; border: none; padding: 7px; font-weight: 600; }
        ''')
        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(18, 14, 18, 12)
        layout.setSpacing(12)
        header = QHBoxLayout()
        title = QLabel('Codex Dispatcher')
        title.setObjectName('title')
        header.addWidget(title)
        header.addStretch()
        self.github_status = QLabel('GitHub · 未检查')
        self.codex_status = QLabel('Codex · 未检查')
        for status in (self.github_status, self.codex_status):
            status.setObjectName('hint')
            header.addWidget(status)
        connection = QPushButton('检查连接')
        connection.clicked.connect(self.check_connections)
        header.addWidget(connection)
        layout.addLayout(header)
        toolbar = QHBoxLayout()
        self.action_buttons = {}
        for key, label, callback in [
            ('new', '+ 新建 Worker', self.new_worker), ('save', '保存', self.save_worker),
            ('test', '测试配置', self.test_configuration), ('start', '开始监视', self.start_selected),
            ('stop', '停止监视', self.stop_selected), ('check', '立即检查', self.check_selected),
            ('dry', '测试运行 / Dry Run', self.dry_run),
        ]:
            button = QPushButton(label)
            button.clicked.connect(callback)
            button.setEnabled(False)
            if key == 'start':
                button.setObjectName('primary')
            self.action_buttons[key] = button
            toolbar.addWidget(button)
        toolbar.addStretch()
        layout.addLayout(toolbar)
        self.splitter = QSplitter(Qt.Vertical)
        top = QSplitter(Qt.Horizontal)
        self.worker_list = QListWidget()
        self.worker_list.setMinimumWidth(210)
        self.worker_list.currentItemChanged.connect(self.select_worker)
        top.addWidget(self.worker_list)
        self.editor = WorkerEditor()
        top.addWidget(self.editor)
        top.setSizes([245, 975])
        self.splitter.addWidget(top)
        self.lower = QTabWidget()
        self.logs = QPlainTextEdit()
        self.logs.setReadOnly(True)
        self.logs.setMaximumBlockCount(500)
        log_panel = QWidget()
        log_layout = QVBoxLayout(log_panel)
        log_layout.setContentsMargins(8, 8, 8, 8)
        log_layout.addWidget(self.logs)
        open_log = QPushButton('查看完整文件日志')
        open_log.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.controller.db.path.parent / 'logs'))))
        log_layout.addWidget(open_log)
        self.lower.addTab(log_panel, '运行日志')
        self.output = QPlainTextEdit()
        self.output.setReadOnly(True)
        self.output.setMaximumBlockCount(2000)
        self.lower.addTab(self.output, 'Codex 实时输出')
        self.dashboard = Dashboard()
        self.lower.addTab(self.dashboard, 'Worker 概览')
        self.history = HistoryView()
        self.lower.addTab(self.history, '任务历史')
        self.splitter.addWidget(self.lower)
        self.splitter.setSizes([555, 260])
        layout.addWidget(self.splitter)
        self.setCentralWidget(central)
        self.statusBar().showMessage('正在加载本地配置…')

        self.editor.refresh_threads.connect(self.refresh_threads)
        self.editor.verify_thread.connect(self.verify_thread)
        self.editor.test_message.connect(self.send_test_message)
        self.editor.test_github.connect(self.test_github)
        self.editor.inspect_project.connect(self.inspect_project)
        self.editor.refresh_models.connect(self.refresh_models)
        self.history.open_issue.connect(self.controller.github.open_issue_in_browser)
        self.history.redispatch.connect(self.redispatch)
        self.history.recovery.connect(self.check_recovery)
        self.history.mark_handled.connect(self.mark_handled)
        self.controller.finished.connect(self.finish_job)
        self.controller.event.connect(self.receive_event)
        self.timer = QTimer(self)
        self.timer.setInterval(2000)
        self.timer.timeout.connect(self.refresh_snapshot)
        self.tray = None
        if tray_enabled and QSystemTrayIcon.isSystemTrayAvailable():
            self.tray = QSystemTrayIcon(self.windowIcon(), self)
            menu = QMenu()
            for label, callback in [('打开 Codex Dispatcher', self.open_window), ('开始全部监视', self.start_all),
                                    ('停止全部监视', self.stop_all), ('立即检查', self.check_all), ('退出', self.quit_app)]:
                action = QAction(label, self)
                action.triggered.connect(callback)
                menu.addAction(action)
            self.tray.setContextMenu(menu)
            self.tray.activated.connect(lambda reason: self.open_window() if reason == QSystemTrayIcon.DoubleClick else None)
            self.tray.setToolTip('Codex Dispatcher · Paused')
            self.tray.show()
        self.controller.initialize()

    def run_job(self, name, function, callback=None, on_error=None):
        if self.controller.submit(name, function):
            self.callbacks[name] = (callback, on_error)
            self.statusBar().showMessage(name + '…')

    def finish_job(self, name, value, error):
        self.controller.acknowledge(name)
        callback, on_error = self.callbacks.pop(name, (None, None))
        if error:
            self.log('ERROR', name + ': ' + error)
            self.statusBar().showMessage(error[:160])
            if on_error:
                on_error(error)
                return
            if name != 'snapshot':
                QMessageBox.warning(self, name, error)
            if name == 'initialize':
                return
        elif name == 'initialize':
            self.ready = True
            for button in self.action_buttons.values():
                button.setEnabled(True)
            self.apply_snapshot(value)
            if value['recovery']:
                self.log('WARNING', f"{value['recovery']} 个残留任务需要恢复检查，请打开任务历史。")
                self.lower.setCurrentWidget(self.history)
            if not self.workers:
                self.new_worker()
            self.timer.start()
            self.check_connections()
        elif name == 'snapshot':
            self.apply_snapshot(value)
        elif callback:
            callback(value)
        if not error and name != 'snapshot':
            self.statusBar().showMessage(name + '完成')
        if name in ('save', 'redispatch', 'recovery', 'mark_handled', 'check', 'start', 'stop'):
            self.refresh_snapshot()

    def refresh_snapshot(self):
        if self.ready:
            self.controller.submit('snapshot', self.controller.snapshot)

    def apply_snapshot(self, snapshot):
        self.workers, self.runtime, self.records = snapshot['workers'], snapshot['runtime'], snapshot['history']
        selected = self._selected_id
        self.worker_list.blockSignals(True)
        self.worker_list.clear()
        for worker in self.workers:
            state = self.runtime.get(worker.id, {})
            label = f"{worker.name}\n{worker.repository}\n{state.get('status', 'Paused')} · Queue {state.get('queue', 0)}"
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, worker.id)
            self.worker_list.addItem(item)
            if worker.id == selected:
                self.worker_list.setCurrentItem(item)
        self.worker_list.blockSignals(False)
        self.dashboard.update_state(self.workers, self.runtime)
        self.history.set_records(self.records)
        if self.tray:
            running = any(r['status'] in ('dispatching','dispatched') for r in self.records)
            error = any(r['status'] == 'recovery_required' for r in self.records)
            monitoring = any(s.get('status') == 'Monitoring' for s in self.runtime.values())
            status = 'Error' if error else 'Running' if running else 'Monitoring' if monitoring else 'Paused'
            self.tray.setToolTip('Codex Dispatcher · ' + status)
        if not selected and self.workers and not self.editor.dirty():
            self.worker_list.setCurrentRow(0)

    def select_worker(self, current, previous):
        if not current:
            return
        if self.editor.dirty():
            answer = QMessageBox.question(self, '未保存的配置', '放弃当前未保存的修改，切换 Worker？')
            if answer != QMessageBox.Yes:
                self.worker_list.blockSignals(True)
                self.worker_list.setCurrentItem(previous)
                self.worker_list.blockSignals(False)
                return
        identifier = current.data(Qt.UserRole)
        worker = next((w for w in self.workers if w.id == identifier), None)
        if worker:
            self._selected_id = identifier
            self.editor.load(worker)

    def new_worker(self):
        if not self.ready:
            return
        if self.editor.dirty() and QMessageBox.question(self, '未保存的配置', '放弃当前修改并新建 Worker？') != QMessageBox.Yes:
            return
        self.worker_list.clearSelection()
        self._selected_id = None
        self.editor.load(Worker())
        self.editor.tabs.setCurrentIndex(0)
        self.editor.fields['name'].setFocus()

    def saved_worker(self):
        worker = next((w for w in self.workers if w.id == self.editor.worker.id), None)
        if not worker or self.editor.dirty():
            QMessageBox.information(self, '先保存配置', '请先保存当前 Worker 配置。')
            return None
        return worker

    def save_worker(self):
        worker = self.editor.collect()
        if self.controller.monitor.is_monitoring(worker.id) or any(r['worker_id'] == worker.id and
            r['status'] in ('queued','dispatching','dispatched','recovery_required') for r in self.records):
            QMessageBox.information(self, '当前 Worker 有任务', '请先停止监视，并处理排队 / 运行 / 恢复任务，再修改配置。')
            return
        def save():
            self.controller.dispatch.validate_worker(worker)
            self.controller.db.save_worker(worker)
            return worker
        def saved(value):
            self._selected_id = value.id
            self.editor.load(value)
            self.log('INFO', 'Worker 配置已保存：' + value.name)
        self.run_job('save', save, saved)

    def test_configuration(self):
        worker = self.editor.collect()
        self.run_job('测试配置', lambda: self.controller.dispatch.test_configuration(worker),
                     lambda result: QMessageBox.information(self, '测试配置', result))

    def test_github(self):
        repository = self.editor.fields['repository'].text()
        self.run_job('测试 GitHub', lambda: self.controller.github.test_repository(repository),
                     lambda repo: QMessageBox.information(self, 'GitHub OK', repo + '\n登录、访问和 Issue 查询通过。'))

    def check_connections(self):
        def check():
            result = {}
            for key, action in [('GitHub', self.controller.github.check_auth), ('Codex', self.controller.codex.check_connection)]:
                try:
                    action()
                    result[key] = 'Connected'
                except Exception as exc:
                    result[key] = 'Not connected · ' + redact(str(exc))[:90]
            return result
        def display(result):
            self.github_status.setText('GitHub · ' + result['GitHub'].split(' · ')[0])
            self.github_status.setToolTip(result['GitHub'])
            self.codex_status.setText('Codex · ' + result['Codex'].split(' · ')[0])
            self.codex_status.setToolTip(result['Codex'])
            for key, value in result.items():
                self.log('INFO' if value == 'Connected' else 'WARNING', key + ': ' + value)
        self.run_job('检查连接', check, display)

    def refresh_threads(self, path):
        if not path.strip() or not Path(path).is_dir():
            QMessageBox.warning(self, '选择项目', '请先选择存在的本地项目目录。')
            return
        def show(threads):
            self.editor.project_info.setText(f'项目：{path}\nCodex 会话：{len(threads)}')
            picker = ThreadPicker(threads, self)
            if picker.exec() and picker.selected_id:
                self.editor.fields['target_thread_id'].setText(picker.selected_id)
        self.run_job('获取项目会话', lambda: self.controller.codex.list_threads(path), show)

    def inspect_project(self, path):
        def show(info):
            self.editor.project_info.setText(f"项目：{info['path']}\nGit remote：{info['remote'] or '—'}\n"
                                             f"Branch：{info['branch'] or '—'}\n{info.get('note', '')}")
        self.run_job('读取项目 Git 信息', lambda: inspect_project(path), show)

    def verify_thread(self):
        worker = self.editor.collect()
        self.run_job('验证会话', lambda: self.controller.codex.validate_thread(worker.target_thread_id,
                     worker.target_project, worker.allow_mismatch),
                     lambda thread: QMessageBox.information(self, 'Thread OK', f'{thread.id}\n{thread.cwd}\n只读验证成功，未发送消息。'))

    def refresh_models(self):
        self.run_job('刷新模型', self.controller.codex.list_models, self.editor.set_models)

    def send_test_message(self):
        worker = self.editor.collect()
        if QMessageBox.question(self, '确认发送测试消息', '这会向指定真实 Codex 会话添加一个 turn，并使用模型额度。\n'
                                f'Thread：{worker.target_thread_id}\n请确认发送“仅回复已收到，不修改任何文件”？') != QMessageBox.Yes:
            return
        def send():
            with ThreadLocks.get(worker.target_thread_id):
                if self.controller.db.thread_blocked(worker.target_thread_id):
                    raise ValueError('目标会话有运行或恢复任务，请先处理它')
                return self.controller.codex.send_task(worker.target_thread_id,
                    '[Codex Dispatcher Test]\n请仅回复已收到，不修改任何文件，不调用工具。', worker.target_project,
                    allow_mismatch=worker.allow_mismatch, trusted=True, on_event=self.controller.emit)
        self.run_job('发送测试消息', send, lambda result: text_dialog(self, '测试回复', result.final_response or result.error))

    def start_selected(self):
        worker = self.saved_worker()
        if worker:
            self.start_workers([worker])

    def start_workers(self, workers):
        def start():
            for worker in workers:
                if worker.enabled:
                    self.controller.dispatch.test_configuration(worker)
                    self.controller.monitor.start(worker)
        self.run_job('start', start)

    def stop_selected(self):
        worker = self.saved_worker()
        if not worker:
            return
        active = worker.target_thread_id in self.controller.codex.active_threads()
        interrupt = False
        if active:
            dialog = QMessageBox(self)
            dialog.setWindowTitle('停止监视')
            dialog.setText('已有任务正在 Codex 中执行。选择停止方式：')
            only = dialog.addButton('只停止后续监视', QMessageBox.AcceptRole)
            both = dialog.addButton('同时请求停止当前任务', QMessageBox.DestructiveRole)
            dialog.addButton('取消', QMessageBox.RejectRole)
            dialog.exec()
            if dialog.clickedButton() not in (only, both):
                return
            interrupt = dialog.clickedButton() == both
        def stop():
            self.controller.monitor.stop(worker.id)
            if interrupt:
                self.controller.codex.interrupt(worker.target_thread_id)
        self.run_job('stop', stop)

    def check_selected(self):
        worker = self.saved_worker()
        if worker:
            self.run_job('check', lambda: self.controller.monitor.check_now(worker))

    def dry_run(self):
        worker = self.editor.collect()
        dialog = QDialog(self)
        dialog.setWindowTitle('Dry Run')
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel('查询 GitHub 并预览最终 Prompt；不会向目标会话发送消息，也不会写成功记录。'))
        normalize = QCheckBox('真实测试廉价整理模型（会消耗额度，默认关闭）')
        normalize.setEnabled(worker.dispatcher_enabled)
        layout.addWidget(normalize)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec():
            use_normalizer = normalize.isChecked()
            def show(prompts):
                text = '\n\n'.join(f"Would dispatch to:\nProject: {p['project']}\nThread ID: {p['thread_id']}\n"
                                   f"Issue: #{p['issue']}\n\n{p['prompt']}" for p in prompts)
                text_dialog(self, 'Dry Run · 未派送', text or '没有新的匹配 Issue（0 模型调用）。')
            self.run_job('Dry Run', lambda: self.controller.dispatch.dry_run(worker, use_normalizer=use_normalizer), show)

    def start_all(self):
        if self.ready:
            self.start_workers(self.workers)

    def stop_all(self):
        if self.ready:
            self.run_job('stop', lambda: [self.controller.monitor.stop(w.id) for w in self.workers])

    def check_all(self):
        if self.ready:
            self.run_job('check', lambda: [self.controller.monitor.check_now(w) for w in self.workers if w.enabled])

    def redispatch(self, identifier):
        def send():
            new_id = self.controller.dispatch.redispatch(identifier)
            record = self.controller.db.record(new_id)
            self.controller.queue.allow(record['worker_id'], record['target_thread_id'])
            return new_id
        self.run_job('redispatch', send)

    def check_recovery(self, identifier):
        self.run_job('recovery', lambda: self.controller.dispatch.check_recovery(identifier),
                     lambda text: QMessageBox.information(self, '恢复检查', text))

    def mark_handled(self, identifier):
        if QMessageBox.question(self, '标记已处理', '请先确认原会话没有未完成的任务。\n标记后会解除该记录对队列的阻塞。确认标记？') == QMessageBox.Yes:
            self.run_job('mark_handled', lambda: self.controller.dispatch.mark_handled(identifier))

    def receive_event(self, event):
        kind, text = event.get('kind', ''), event.get('text', '')
        if kind in ('message', 'tool', 'status'):
            self.output.moveCursor(self.output.textCursor().MoveOperation.End)
            prefix = event.get('record_id') or event.get('worker_id') or '测试'
            if prefix != self._stream_prefix:
                self.output.insertPlainText('\n\n[' + prefix + ']\n')
                self._stream_prefix = prefix
            self.output.insertPlainText(text if kind == 'message' else '\n' + text + '\n')
        elif kind != 'checked':
            self.log('ERROR' if kind == 'error' else 'INFO', text)
            if kind == 'running':
                self.lower.setCurrentWidget(self.output)
        self.statusBar().showMessage(text[:160])

    def log(self, level, text):
        self.logs.appendPlainText(f'{datetime.now().astimezone():%H:%M:%S}  {level}  {redact(text)}')
        logging.getLogger('codex_dispatcher').log(logging.WARNING if level == 'WARNING' else logging.ERROR if level == 'ERROR' else logging.INFO, redact(text))

    def open_window(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def quit_app(self):
        if self.ready:
            for worker in self.workers:
                self.controller.monitor.stop(worker.id)
        active = self.controller.queue.running() if self.ready else []
        pending = self.controller._jobs - {'snapshot', '检查连接'}
        if active or pending:
            QMessageBox.information(self, '等待任务安全结束', '已停止后续监视。当前任务或后台操作仍在执行，应用将保持运行。\n'
                                    '可在“停止监视”中请求安全停止当前任务，完成后再退出。')
            return
        self._force_close = True
        self.close()

    def closeEvent(self, event):
        if not self._force_close and self.tray:
            self.hide()
            event.ignore()
            return
        if not self._force_close and (self.controller._jobs - {'snapshot', '检查连接'} or
                                     self.ready and self.controller.queue.running()):
            event.ignore()
            self.quit_app()
            return
        self.timer.stop()
        self.controller.shutdown()
        if self.tray:
            self.tray.hide()
        event.accept()
        QApplication.instance().quit()
