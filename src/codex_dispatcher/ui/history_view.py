from PySide6.QtCore import Signal
from PySide6.QtWidgets import (QApplication, QDialog, QHBoxLayout, QLabel, QPlainTextEdit,
                              QPushButton, QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget)


def text_dialog(parent, title, text):
    dialog = QDialog(parent)
    dialog.setWindowTitle(title)
    dialog.resize(940, 650)
    layout = QVBoxLayout(dialog)
    editor = QPlainTextEdit()
    editor.setReadOnly(True)
    editor.setPlainText(text)
    layout.addWidget(editor)
    close = QPushButton('关闭')
    close.clicked.connect(dialog.accept)
    layout.addWidget(close)
    dialog.exec()


class HistoryView(QWidget):
    redispatch = Signal(str)
    recovery = Signal(str)
    mark_handled = Signal(str)
    open_issue = Signal(str, int)

    def __init__(self):
        super().__init__()
        self.records = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(['时间', 'Worker', 'Repository', 'Issue', 'Thread', '状态', '结果'])
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.SingleSelection)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.doubleClicked.connect(self.details)
        layout.addWidget(self.table)
        actions = QHBoxLayout()
        for title, function in [('详情 / Prompt / 回复', self.details), ('查看 Issue', self.open),
                                ('复制 Thread ID', self.copy), ('重新派送', lambda: self.act(self.redispatch)),
                                ('检查恢复状态', lambda: self.act(self.recovery)), ('标记已处理', lambda: self.act(self.mark_handled))]:
            button = QPushButton(title)
            button.clicked.connect(function)
            actions.addWidget(button)
        layout.addLayout(actions)

    def set_records(self, records):
        selected = self.current()
        selected_id = selected['id'] if selected else None
        self.records = records
        self.table.setRowCount(len(records))
        for row, record in enumerate(records):
            values = [record['dispatch_time'] or record['discovered_at'], record['worker_name'], record['repository'],
                      '#' + str(record['issue_number']), record['target_thread_id'], record['status'],
                      record['error'] or record['final_response'] or '']
            for col, value in enumerate(values):
                item = QTableWidgetItem(value.replace('\n', ' ')[:160])
                item.setToolTip(value)
                self.table.setItem(row, col, item)
            if record['id'] == selected_id:
                self.table.selectRow(row)
        for col, width in enumerate([190, 160, 150, 65, 260, 130, 350]):
            self.table.setColumnWidth(col, width)

    def current(self):
        row = self.table.currentRow()
        return self.records[row] if 0 <= row < len(self.records) else None

    def act(self, signal):
        record = self.current()
        if record:
            signal.emit(record['id'])

    def details(self):
        record = self.current()
        if record:
            text_dialog(self, '派送详情', f"状态：{record['status']}\nDispatch ID：{record['id']}\nTurn ID：{record['turn_id'] or ''}\n"
                        f"错误：{record['error']}\n\n=== 派送 Prompt ===\n{record['prompt']}\n\n=== Codex 最终回复 ===\n{record['final_response']}")

    def copy(self):
        record = self.current()
        if record:
            QApplication.clipboard().setText(record['target_thread_id'])

    def open(self):
        record = self.current()
        if record:
            self.open_issue.emit(record['repository'], record['issue_number'])
