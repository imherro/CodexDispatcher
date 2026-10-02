import json
from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QApplication, QDialog, QDialogButtonBox, QHBoxLayout, QLabel,
                              QLineEdit, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout)


class ThreadPicker(QDialog):
    def __init__(self, threads, parent=None):
        super().__init__(parent)
        self.setWindowTitle('选择 Codex 会话（Thread）')
        self.resize(1060, 460)
        self.threads = threads
        self.selected_id = None
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f'此项目找到 {len(threads)} 个已保存会话。仅显示运行时实际提供的字段。'))
        search = QLineEdit()
        search.setPlaceholderText('搜索标题、Thread ID、模型、cwd…')
        search.textChanged.connect(self.filter)
        layout.addWidget(search)
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(['标题 / 首条摘要', 'Thread ID', '更新时间', '模型', 'cwd', 'source'])
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.SingleSelection)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.doubleClicked.connect(self.choose)
        layout.addWidget(self.table)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.choose)
        buttons.rejected.connect(self.reject)
        copy = QPushButton('复制所选 Thread ID')
        copy.clicked.connect(self.copy)
        bottom = QHBoxLayout()
        bottom.addWidget(copy)
        bottom.addStretch()
        bottom.addWidget(buttons)
        layout.addLayout(bottom)
        self.populate()

    def populate(self):
        self.table.setRowCount(len(self.threads))
        for row, thread in enumerate(self.threads):
            updated = datetime.fromtimestamp(thread.updated_at).astimezone().strftime('%Y-%m-%d %H:%M') if thread.updated_at else ''
            values = [thread.name or thread.preview[:90], thread.id, updated, thread.model or '',
                      thread.cwd, json.dumps(thread.source, ensure_ascii=False) if isinstance(thread.source, dict) else thread.source or '']
            for col, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(value)
                self.table.setItem(row, col, item)
        self.table.setColumnWidth(0, 240)
        self.table.setColumnWidth(1, 270)
        self.table.setColumnWidth(2, 145)
        self.table.setColumnWidth(3, 120)

    def filter(self, query):
        for row in range(self.table.rowCount()):
            text = ' '.join(self.table.item(row, col).text() for col in range(6)).casefold()
            self.table.setRowHidden(row, query.casefold() not in text)

    def choose(self):
        row = self.table.currentRow()
        if row >= 0:
            self.selected_id = self.threads[row].id
            self.accept()

    def copy(self):
        row = self.table.currentRow()
        if row >= 0:
            QApplication.clipboard().setText(self.threads[row].id)
