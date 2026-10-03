from __future__ import annotations

from html import escape
import logging
from PySide6.QtCore import Qt, QTimer, QPointF, QSize
from PySide6.QtGui import QAction, QColor, QIcon, QPainter, QPixmap, QPen, QPolygonF
from PySide6.QtWidgets import (QApplication, QDialog, QDialogButtonBox, QHBoxLayout,
    QLabel, QMainWindow, QMenu, QMessageBox, QPushButton, QSystemTrayIcon,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget, QToolButton, QHeaderView)
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


def action_icon(kind, color='#233043'):
    pixmap = QPixmap(48, 48)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.scale(2, 2)
    painter.setPen(QPen(QColor(color), 1.8, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    painter.setBrush(QColor(color))
    if kind == 'start':
        painter.drawPolygon(QPolygonF([QPointF(8, 5), QPointF(19, 12), QPointF(8, 19)]))
    elif kind == 'stop':
        painter.drawRoundedRect(6, 6, 12, 12, 1, 1)
    elif kind == 'edit':
        painter.setBrush(Qt.NoBrush)
        painter.drawPolygon(QPolygonF([QPointF(5, 15), QPointF(15, 5), QPointF(19, 9),
                                      QPointF(9, 19), QPointF(4, 20)]))
        painter.drawLine(13, 7, 17, 11)
    else:
        painter.setBrush(Qt.NoBrush)
        painter.drawArc(5, 5, 14, 14, 35 * 16, 290 * 16)
        painter.drawPolyline(QPolygonF([QPointF(19, 4), QPointF(19, 10), QPointF(13, 10)]))
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
        self.worker_actions = {}
        self.editor = WorkerEditor()
        self.editor_dialog = None
        self.editor.refresh_threads.connect(self.refresh_threads)
        self.editor.refresh_repositories.connect(self.refresh_repositories)
        self.setWindowTitle('Codex Dispatcher v' + __version__)
        self.setWindowIcon(app_icon())
        self.resize(1080, 570)
        self.setMinimumSize(980, 440)
        self.setStyleSheet("""
            QMainWindow { background: #f4f6f8; }
            QWidget { font-family: "Segoe UI", "Microsoft YaHei UI", "SimHei"; font-size: 13px; color: #233043; }
            QPushButton { background: white; border: 1px solid #ced6df; border-radius: 6px; padding: 8px 16px; }
            QPushButton:hover { background: #eaf1f5; }
            QPushButton:disabled { color: #8895a5; background: #eef1f4; }
            QPushButton#primary { background: #176b63; color: white; border-color: #176b63; }
            QPushButton#primary:disabled, QPushButton#danger:disabled { background: #eef1f4; color: #8895a5; border-color: #ced6df; }
            QPushButton#danger { background: #b42318; color: white; border-color: #b42318; }
            QToolButton { background: white; border: 1px solid #ced6df; border-radius: 6px; padding: 0; }
            QToolButton:hover { background: #eaf1f5; }
            QToolButton#primary { background: #176b63; border-color: #176b63; }
            QToolButton#danger { background: #b42318; border-color: #b42318; }
            QToolButton:disabled { background: #eef1f4; border-color: #ced6df; }
            QLineEdit, QComboBox, QSpinBox { background: white; border: 1px solid #ced6df; border-radius: 4px; padding: 6px; }
            QPlainTextEdit { background: #ffffff; color: #233043; border: 1px solid #ced6df; border-radius: 4px; padding: 6px;
                             selection-background-color: #176b63; selection-color: #ffffff; }
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
        self.worker_table = QTableWidget(0, 7)
        self.worker_table.setHorizontalHeaderLabels(['Worker / 仓库', '分配规则', 'Agent 会话', '监测状态', '下次检查', '最近通知', '操作'])
        self.worker_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.worker_table.setSelectionMode(QTableWidget.SingleSelection)
        self.worker_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.worker_table.verticalHeader().hide()
        self.worker_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        for col, width in enumerate((205, 140, 165, 80, 85, 125, 118)):
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
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self.refresh_snapshot)
        self.tray = None
        if tray_enabled and QSystemTrayIcon.isSystemTrayAvailable():
            self.tray = QSystemTrayIcon(self.windowIcon(), self)
            menu = QMenu()
            for label, callback in (('打开', self.open_window), ('退出', self.quit_app)):
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
            self.update_worker_actions()

    def finish_job(self, name, value, error):
        self.controller.acknowledge(name)
        if self.controller.closing:
            return
        callback, on_error = self.callbacks.pop(name, (None, None))
        if error:
            logging.getLogger('codex_dispatcher').error('%s: %s', name, error)
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
        if name in ('save', 'redispatch', 'recovery', 'mark_handled') or name.startswith(('start:', 'stop:', 'check:')):
            self.refresh_snapshot()
        self.update_worker_actions()

    def refresh_snapshot(self):
        if self.ready:
            # Countdown stays live even if other network jobs occupy the UI pool.
            for row, worker in enumerate(self.workers):
                seconds = self.controller.monitor.countdown(worker.id)
                item = self.worker_table.item(row, 4)
                if item:
                    item.setText('—' if seconds is None else '检查中…' if seconds == 0 else f'{seconds // 60:02d}:{seconds % 60:02d}')
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
            seconds = self.runtime.get(worker.id, {}).get('countdown')
            countdown = '—' if seconds is None else '检查中…' if seconds == 0 else f'{seconds // 60:02d}:{seconds % 60:02d}'
            rule = '@' + worker.assignment_value.removeprefix('@') if worker.assignment_mode == 'mention' else worker.assignment_mode + ': ' + worker.assignment_value
            values = [worker.name + '\n' + worker.repository, rule,
                      worker.target_thread_name or worker.target_thread_id, state, countdown, result]
            for col, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(worker.target_thread_id if col == 2 else value if col != 5 or not last else value + '\n' + (last['error'] or last['dispatch_time'] or ''))
                self.worker_table.setItem(row, col, item)
            if worker.id not in self.worker_actions:
                panel = QWidget()
                buttons = {}
                actions = QHBoxLayout(panel)
                actions.setContentsMargins(5, 6, 5, 6)
                actions.setSpacing(5)
                for key, label, callback in (('edit', '编辑', self.edit_worker),
                                             ('monitor', '开始监测', self.toggle_worker),
                                             ('check', '立即检查', self.check_worker)):
                    button = QToolButton()
                    button.setToolButtonStyle(Qt.ToolButtonIconOnly)
                    button.setFixedSize(32, 32)
                    button.setIconSize(QSize(20, 20))
                    button.setIcon(action_icon(key))
                    button.setToolTip(label)
                    button.setAccessibleName(label)
                    button.clicked.connect(lambda checked=False, wid=worker.id, action=callback: action(wid))
                    buttons[key] = button
                    actions.addWidget(button)
                buttons['check'].setToolTip('立即查询并通知新的待办；无需开启持续监测。')
                self.worker_actions[worker.id] = buttons
                self.worker_table.setCellWidget(row, 6, panel)
            self.worker_table.setRowHeight(row, 60)
            if worker.id == self._selected_id:
                self.worker_table.selectRow(row)
        self.worker_table.blockSignals(False)
        self.summary.setText(f'{len(self.workers)} 个 Worker · {sum(self.controller.monitor.is_monitoring(w.id) for w in self.workers)} 个监测中')
        self.empty_hint.setVisible(not self.workers)
        self.history.set_records(self.records)
        if self.workers and not self._selected_id:
            self.worker_table.selectRow(0)
        self.update_worker_actions()

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
        dialog.resize(700, 650)
        layout = QVBoxLayout(dialog)
        layout.addWidget(self.editor)
        # Closing hides the reused editor explicitly; reparenting does not
        # clear that hidden state when another configuration dialog opens.
        self.editor.show()
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
        self.refresh_repositories()

    def refresh_repositories(self):
        if '读取仓库' in self.controller._jobs:
            return
        self.editor.repositories_loading()
        self.run_job('读取仓库', self.controller.github.list_repositories, self.editor.set_repositories,
                     on_error=self.editor.repository_error)

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
            (r['status'] in ('dispatching', 'dispatched', 'recovery_required') or r['status'] == 'notified' and not r['finished_at']) for r in self.records):
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

    def worker(self, identifier):
        return next((w for w in self.workers if w.id == identifier), None)

    def edit_worker(self, identifier):
        worker = self.worker(identifier)
        if worker:
            self.open_editor(worker)

    def update_worker_actions(self):
        for worker in self.workers:
            buttons = self.worker_actions.get(worker.id)
            if not buttons:
                continue
            monitoring = self.ready and self.controller.monitor.is_monitoring(worker.id)
            starting = 'start:' + worker.id in self.controller._jobs
            stopping = 'stop:' + worker.id in self.controller._jobs
            checking = 'check:' + worker.id in self.controller._jobs
            button = buttons['monitor']
            label = '正在开始…' if starting else '正在停止…' if stopping else '停止监测' if monitoring else '开始监测'
            button.setToolTip(label)
            button.setAccessibleName(label)
            button.setIcon(action_icon('stop' if monitoring else 'start', '#ffffff'))
            button.setEnabled(self.ready and worker.enabled and not starting and not stopping)
            buttons['edit'].setEnabled(self.ready and not starting and not stopping and not checking)
            buttons['check'].setToolTip('检查中…' if checking else '立即检查：查询并通知新待办，无需开启持续监测。')
            buttons['check'].setAccessibleName('检查中…' if checking else '立即检查')
            buttons['check'].setEnabled(self.ready and worker.enabled and not checking and not starting and not stopping)
            style = 'danger' if monitoring else 'primary'
            if button.objectName() != style:
                button.setObjectName(style)
                button.style().unpolish(button)
                button.style().polish(button)

    def toggle_worker(self, identifier):
        worker = self.worker(identifier)
        if not self.ready or not worker:
            return
        if self.controller.monitor.is_monitoring(identifier):
            self.run_job('stop:' + identifier, lambda: self.controller.monitor.stop(identifier))
        else:
            def start():
                self.controller.dispatch.test_configuration(worker)
                self.controller.db.save_worker(worker)
                self.controller.monitor.start(worker)
            self.run_job('start:' + identifier, start)

    def check_worker(self, identifier):
        worker = self.worker(identifier)
        if self.ready and worker and worker.enabled:
            self.run_job('check:' + identifier, lambda: self.controller.monitor.check_now(worker))

    def show_history(self):
        dialog = QDialog(self)
        dialog.setWindowTitle('通知记录')
        dialog.resize(1120, 500)
        layout = QVBoxLayout(dialog)
        layout.addWidget(self.history)
        self.history.show()
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
        self.editor.dispose()
        self.history.deleteLater()
        event.accept()
        QApplication.instance().quit()
