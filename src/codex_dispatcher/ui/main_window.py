from __future__ import annotations

from html import escape
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction, QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (QApplication, QDialog, QDialogButtonBox, QHBoxLayout,
    QLabel, QMainWindow, QMenu, QMessageBox, QPushButton, QSystemTrayIcon,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)
from codex_dispatcher import __version__
from codex_dispatcher.domain.models import Worker
from .history_view import HistoryView
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
        self.ready = self._force_close = False
        self._selected_id = None
        self.editor = WorkerEditor()
        self.editor_dialog = None
        self.editor.refresh_threads.connect(self.refresh_threads)
        self.setWindowTitle('Codex Dispatcher v' + __version__)
        self.setWindowIcon(app_icon())
        self.resize(1060, 570)
        self.setMinimumSize(850, 440)
        self.setStyleSheet("""
            QMainWindow { background: #f4f6f8; }
            QWidget { font-family: "Segoe UI", "Microsoft YaHei UI", "SimHei"; font-size: 13px; color: #233043; }
            QPushButton { background: white; border: 1px solid #ced6df; border-radius: 6px; padding: 8px 16px; }
            QPushButton:hover { background: #eaf1f5; }
            QPushButton:disabled { color: #8895a5; background: #eef1f4; }
            QPushButton#primary { background: #176b63; color: white; border-color: #176b63; }
            QPushButton#danger { background: #b42318; color: white; border-color: #b42318; }
            QLineEdit, QComboBox, QSpinBox { background: white; border: 1px solid #ced6df; border-radius: 4px; padding: 6px; }
            QTableWidget { background: white; border: 1px solid #d9e0e8; selection-background-color: #e3f1ee; selection-color: #233043; }
            QHeaderView::section { background: #edf1f5; border: none; padding: 10px; font-weight: 600; }
            QLabel#title { font-size: 23px; font-weight: 600; }
            QLabel#hint { color: #68768a; }
        """)
        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(22, 18, 22, 14)
        layout.setSpacing(16)
        header = QHBoxLayout()
        title = QLabel('Codex Dispatcher')
        title.setObjectName('title')
        header.addWidget(title)
        header.addStretch()
        self.github_status, self.codex_status = QLabel(), QLabel()
        for status, name in ((self.github_status, 'GitHub'), (self.codex_status, 'Codex')):
            self.set_connection_status(status, name, '未检查')
            header.addWidget(status)
        connection = QPushButton('检查连接')
        connection.clicked.connect(self.check_connections)
        header.addWidget(connection)
        layout.addLayout(header)
        hint = QLabel('发现新待办 → 通知对应 agent 会话。没有新待办时保持安静。')
        hint.setObjectName('hint')
        layout.addWidget(hint)
        toolbar = QHBoxLayout()
        self.action_buttons = {}
        for key, label, callback in (
            ('new', '+ 添加 Worker', self.new_worker),
            ('edit', '编辑', self.edit_selected),
            ('monitor', '开始监测', self.toggle_monitoring),
        ):
            button = QPushButton(label)
            button.clicked.connect(callback)
            button.setEnabled(False)
            self.action_buttons[key] = button
            toolbar.addWidget(button)
        toolbar.addStretch()
        self.summary = QLabel('0 个 Worker')
        toolbar.addWidget(self.summary)
        layout.addLayout(toolbar)
        self.worker_table = QTableWidget(0, 5)
        self.worker_table.setHorizontalHeaderLabels(['Worker / 仓库', '分配规则', 'Agent 会话', '监测状态', '最近通知'])
        self.worker_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.worker_table.setSelectionMode(QTableWidget.SingleSelection)
        self.worker_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.worker_table.verticalHeader().hide()
        self.worker_table.horizontalHeader().setStretchLastSection(True)
        for col, width in enumerate((250, 170, 210, 100)):
            self.worker_table.setColumnWidth(col, width)
        self.worker_table.itemSelectionChanged.connect(self.select_worker)
        self.worker_table.doubleClicked.connect(self.edit_selected)
        layout.addWidget(self.worker_table, 1)
        footer = QHBoxLayout()
        self.empty_hint = QLabel('点击“添加 Worker”，绑定仓库、分配规则和 agent 会话。')
        self.empty_hint.setObjectName('hint')
        footer.addWidget(self.empty_hint)
        footer.addStretch()
        history_button = QPushButton('通知记录…')
        history_button.clicked.connect(self.show_history)
        footer.addWidget(history_button)
        layout.addLayout(footer)
        self.setCentralWidget(central)
        self.history = HistoryView()
        self.history.open_issue.connect(controller.github.open_issue_in_browser)
        self.history.redispatch.connect(self.redispatch)
        self.history.recovery.connect(self.check_recovery)
        self.history.mark_handled.connect(self.mark_handled)
        controller.finished.connect(self.finish_job)
        controller.event.connect(self.receive_event)
        self.timer = QTimer(self)
        self.timer.setInterval(2000)
        self.timer.timeout.connect(self.refresh_snapshot)
        self.tray = None
        if tray_enabled and QSystemTrayIcon.isSystemTrayAvailable():
            self.tray = QSystemTrayIcon(self.windowIcon(), self)
            menu = QMenu()
            for label, callback in (('打开', self.open_window), ('开始 / 停止监测', self.toggle_monitoring), ('退出', self.quit_app)):
                action = QAction(label, self)
                action.triggered.connect(callback)
                menu.addAction(action)
            self.tray.setContextMenu(menu)
            self.tray.activated.connect(lambda reason: self.open_window() if reason == QSystemTrayIcon.DoubleClick else None)
            self.tray.setToolTip('Codex Dispatcher')
            self.tray.show()
        self.statusBar().showMessage('正在加载配置…')
        controller.initialize()

    @staticmethod
    def set_connection_status(widget, name, status):
        color = '#16803c' if status == 'Connected' else '#b42318' if status == 'Not connected' else '#8895a5'
        widget.setText(f'<span style="color:{color}">●</span> {escape(name)} · {escape(status)}')

    def run_job(self, name, function, callback=None, on_error=None):
        if self.controller.submit(name, function):
            self.callbacks[name] = (callback, on_error)
            self.update_monitor_button()

    def finish_job(self, name, value, error):
        self.controller.acknowledge(name)
        callback, on_error = self.callbacks.pop(name, (None, None))
        if error:
            self.statusBar().showMessage(error[:180])
            if on_error:
                on_error(error)
            elif name != 'snapshot':
                QMessageBox.warning(self, '操作未完成', error)
        elif name == 'initialize':
            self.ready = True
            self.action_buttons['new'].setEnabled(True)
            self.apply_snapshot(value)
            if value['recovery']:
                self.statusBar().showMessage('有发送结果不确定的旧记录，请在“通知记录”中确认。')
            else:
                self.statusBar().clearMessage()
            self.timer.start()
            self.check_connections()
        elif name == 'snapshot':
            self.apply_snapshot(value)
        elif callback:
            callback(value)
        if name in ('save', 'start', 'stop', 'redispatch', 'recovery', 'mark_handled'):
            self.refresh_snapshot()
        self.update_monitor_button()

    def refresh_snapshot(self):
        if self.ready:
            self.controller.submit('snapshot', self.controller.snapshot)

    def apply_snapshot(self, snapshot):
        self.workers, self.runtime, self.records = snapshot['workers'], snapshot['runtime'], snapshot['history']
        self.worker_table.blockSignals(True)
        self.worker_table.setRowCount(len(self.workers))
        for row, worker in enumerate(self.workers):
            monitoring = self.controller.monitor.is_monitoring(worker.id)
            records = [r for r in self.records if r['worker_id'] == worker.id]
            last = records[0] if records else None
            state = '监测中' if monitoring else '已暂停' if worker.enabled else '已禁用'
            result = '—'
            if last:
                status = {'queued': '等待通知', 'dispatching': '正在通知', 'dispatched': '已通知', 'notified': '已通知',
                          'completed': '已通知', 'failed': '通知失败', 'ignored': '已跳过', 'recovery_required': '待确认'}.get(last['status'], last['status'])
                result = f"#{last['issue_number']} · {status}"
                if last['status'] == 'notified' and last['error'] and not last['finished_at']:
                    result += ' · 待确认'
            values = [worker.name + '\n' + worker.repository, worker.assignment_mode + ': ' + worker.assignment_value,
                      worker.target_thread_name or worker.target_thread_id, state, result]
            for col, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(worker.target_thread_id if col == 2 else value if col != 4 or not last else value + '\n' + (last['error'] or last['dispatch_time'] or ''))
                self.worker_table.setItem(row, col, item)
            self.worker_table.setRowHeight(row, 60)
            if worker.id == self._selected_id:
                self.worker_table.selectRow(row)
        self.worker_table.blockSignals(False)
        self.action_buttons['edit'].setEnabled(self.ready and bool(self.workers))
        self.summary.setText(f'{len(self.workers)} 个 Worker · {sum(self.controller.monitor.is_monitoring(w.id) for w in self.workers)} 个监测中')
        self.empty_hint.setVisible(not self.workers)
        self.history.set_records(self.records)
        if self.workers and not self._selected_id:
            self.worker_table.selectRow(0)
        self.update_monitor_button()

    def select_worker(self):
        row = self.worker_table.currentRow()
        if 0 <= row < len(self.workers):
            self._selected_id = self.workers[row].id

    def open_editor(self, worker):
        if self.editor_dialog:
            self.editor_dialog.raise_()
            return
        self.editor.load(worker)
        dialog = QDialog(self)
        dialog.setWindowTitle('配置 Worker')
        dialog.resize(650, 350)
        layout = QVBoxLayout(dialog)
        layout.addWidget(self.editor)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Save).setText('保存')
        buttons.button(QDialogButtonBox.Cancel).setText('取消')
        buttons.accepted.connect(self.save_worker)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        def closed():
            self.editor.setParent(None)
            self.editor.hide()
            self.editor_dialog = None
            dialog.deleteLater()
        dialog.finished.connect(closed)
        self.editor_dialog = dialog
        dialog.setModal(True)
        dialog.show()

    def new_worker(self):
        if self.ready:
            self.open_editor(Worker())

    def edit_selected(self):
        worker = next((w for w in self.workers if w.id == self._selected_id), None)
        if worker:
            self.open_editor(worker)

    def save_worker(self):
        worker = self.editor.collect()
        if self.controller.monitor.is_monitoring(worker.id) or any(r['worker_id'] == worker.id and
            (r['status'] in ('queued', 'dispatching', 'dispatched', 'recovery_required') or r['status'] == 'notified' and not r['finished_at']) for r in self.records):
            QMessageBox.information(self, '暂不能修改', '请先停止监测，并等待已通知会话结束、处理待确认记录。')
            return
        def save():
            self.controller.dispatch.validate_worker(worker)
            self.controller.github.test_repository(worker.repository)
            self.controller.db.save_worker(worker)
            return worker
        def saved(value):
            self._selected_id = value.id
            self.editor.load(value)
            if self.editor_dialog:
                self.editor_dialog.accept()
            self.statusBar().showMessage('Worker 已保存')
        self.run_job('save', save, saved)

    def refresh_threads(self, project=''):
        def choose(threads):
            picker = ThreadPicker(threads, self.editor_dialog or self)
            if picker.exec() and picker.selected_id:
                self.editor.fields['target_thread_id'].setText(picker.selected_id)
        self.run_job('读取会话', lambda: self.controller.codex.list_threads(), choose)

    def check_connections(self):
        for widget, name in ((self.github_status, 'GitHub'), (self.codex_status, 'Codex')):
            self.set_connection_status(widget, name, '检查中')
        def check():
            result = {}
            for name, action in (('GitHub', self.controller.github.check_auth), ('Codex', self.controller.codex.check_connection)):
                try:
                    action()
                    result[name] = ('Connected', '')
                except Exception as exc:
                    result[name] = ('Not connected', str(exc))
            return result
        def apply(value):
            for widget, name in ((self.github_status, 'GitHub'), (self.codex_status, 'Codex')):
                status, error = value[name]
                self.set_connection_status(widget, name, status)
                widget.setToolTip(error)
        self.run_job('检查连接', check, apply)

    def update_monitor_button(self):
        button = self.action_buttons['monitor']
        monitoring = self.ready and any(self.controller.monitor.is_monitoring(w.id) for w in self.workers)
        pending = self.controller._jobs & {'start', 'stop'}
        button.setText('正在开始…' if 'start' in pending else '正在停止…' if 'stop' in pending else '停止监测' if monitoring else '开始监测')
        button.setEnabled(self.ready and bool(self.workers) and not pending)
        style = 'danger' if monitoring else 'primary'
        if button.objectName() != style:
            button.setObjectName(style)
            button.style().unpolish(button)
            button.style().polish(button)

    def toggle_monitoring(self):
        if not self.ready:
            return
        if any(self.controller.monitor.is_monitoring(w.id) for w in self.workers):
            self.stop_all()
        else:
            self.start_all()

    def start_all(self):
        def start():
            enabled = [w for w in self.workers if w.enabled]
            if not enabled:
                raise ValueError('请先启用至少一个 Worker')
            for worker in enabled:
                self.controller.dispatch.test_configuration(worker)
                self.controller.db.save_worker(worker)
            for worker in enabled:
                self.controller.monitor.start(worker)
        if self.ready:
            self.run_job('start', start)

    def stop_all(self):
        if self.ready:
            self.run_job('stop', lambda: [self.controller.monitor.stop(w.id) for w in self.workers])

    def show_history(self):
        dialog = QDialog(self)
        dialog.setWindowTitle('通知记录')
        dialog.resize(1120, 500)
        layout = QVBoxLayout(dialog)
        layout.addWidget(self.history)
        dialog.exec()
        self.history.setParent(None)
        self.history.hide()

    def redispatch(self, identifier):
        def send():
            new_id = self.controller.dispatch.redispatch(identifier)
            record = self.controller.db.record(new_id)
            self.controller.queue.allow(record['worker_id'], record['target_thread_id'])
        self.run_job('redispatch', send)

    def check_recovery(self, identifier):
        self.run_job('recovery', lambda: self.controller.dispatch.check_recovery(identifier),
                     lambda text: QMessageBox.information(self, '检查结果', text))

    def mark_handled(self, identifier):
        if QMessageBox.question(self, '确认通知状态', '请先确认原会话已收到通知或没有未完成的任务，确认后解除队列阻塞？') == QMessageBox.Yes:
            self.run_job('mark_handled', lambda: self.controller.dispatch.mark_handled(identifier))

    def receive_event(self, event):
        if event.get('kind') in ('message', 'tool', 'status', 'checked'):
            return
        text = event.get('text', '')
        self.statusBar().showMessage(text[:180])
        if self.tray and event.get('kind') == 'error':
            self.tray.showMessage('Codex Dispatcher', text[:180], QSystemTrayIcon.Warning)
        self.refresh_snapshot()

    def open_window(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def quit_app(self):
        if self.ready:
            for worker in self.workers:
                self.controller.monitor.stop(worker.id)
        active = self.controller.queue.running() if self.ready else []
        if active or self.controller._jobs - {'snapshot', '检查连接'}:
            QMessageBox.information(self, '会话仍在运行', '已停止监测。已通知的 agent 或后台操作仍在运行，结束后可退出；关闭窗口可驻留托盘。')
            return
        self._force_close = True
        self.close()

    def closeEvent(self, event):
        if not self._force_close and self.tray:
            self.hide()
            event.ignore()
            return
        if not self._force_close and (self.controller._jobs - {'snapshot', '检查连接'} or self.ready and self.controller.queue.running()):
            event.ignore()
            self.quit_app()
            return
        self.timer.stop()
        self.controller.shutdown()
        if self.tray:
            self.tray.hide()
        self.editor.deleteLater()
        self.history.deleteLater()
        event.accept()
        QApplication.instance().quit()
