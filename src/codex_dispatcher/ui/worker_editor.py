from dataclasses import replace
from PySide6.QtCore import Signal
from PySide6.QtWidgets import QCheckBox, QComboBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QSpinBox, QWidget
from codex_dispatcher.domain.models import Worker


class WorkerEditor(QWidget):
    refresh_threads = Signal(str)
    refresh_repositories = Signal()

    def __init__(self):
        super().__init__()
        self.worker, self.fields = Worker(), {}
        form = QFormLayout(self)
        form.setSpacing(12)
        for key, title, placeholder in (
            ('name', 'Worker 名称', '例如：修电脑 agent'),
            ('repository', 'GitHub 仓库', 'owner/repository 或 GitHub 仓库地址'),
            ('assignment_value', '分配规则', '例如：agent:repair 或 GitHub 用户名'),
            ('target_thread_id', 'Agent 会话', '选择会话，或粘贴 Thread ID'),
        ):
            field = QComboBox() if key == 'repository' else QLineEdit()
            if key != 'repository':
                field.setPlaceholderText(placeholder)
            self.fields[key] = field
            if key == 'assignment_value':
                self.fields['assignment_mode'] = combo = QComboBox()
                combo.addItem('@ 提及', 'mention')
                combo.addItem('Label', 'label')
                combo.addItem('Assignee', 'assignee')
                combo.currentIndexChanged.connect(self.update_assignment_hint)
                row = QHBoxLayout()
                row.addWidget(combo)
                row.addWidget(field, 1)
                form.addRow(title, row)
            elif key == 'repository':
                refresh = QPushButton('刷新仓库')
                refresh.clicked.connect(self.refresh_repositories)
                row = QHBoxLayout()
                row.addWidget(field, 1)
                row.addWidget(refresh)
                form.addRow(title, row)
            elif key == 'target_thread_id':
                choose = QPushButton('选择会话…')
                choose.clicked.connect(lambda: self.refresh_threads.emit(''))
                row = QHBoxLayout()
                row.addWidget(field, 1)
                row.addWidget(choose)
                form.addRow(title, row)
            else:
                form.addRow(title, field)
        self.fields['enabled'] = enabled = QCheckBox('启用此 Worker')
        form.addRow('', enabled)
        self.fields['poll_interval'] = interval = QSpinBox()
        interval.setRange(1, 60)
        interval.setSuffix(' 分钟')
        form.addRow('检查间隔', interval)
        form.addRow('', QLabel('匹配新待办后，只通知此会话自行读取和处理 Issue。'))
        self.load(self.worker)

    def load(self, worker):
        self.worker = replace(worker)
        for key, field in self.fields.items():
            value = getattr(worker, key)
            if isinstance(field, QCheckBox):
                field.setChecked(value)
            elif isinstance(field, QSpinBox):
                field.setValue(value)
            elif isinstance(field, QComboBox):
                if key == 'repository' and field.findData(value) == -1:
                    field.addItem(value or '请选择 GitHub 仓库', value)
                field.setCurrentIndex(field.findData(value))
            else:
                field.setText(value)
        self.update_assignment_hint()

    def update_assignment_hint(self):
        field = self.fields.get('assignment_value')
        if field:
            mode = self.fields['assignment_mode'].currentData()
            field.setPlaceholderText({'mention':'例如：@codex-1070-rc（可省略 @）',
                                      'label':'例如：agent:repair', 'assignee':'GitHub 用户名，例如 imherro'}.get(mode, ''))

    def set_repositories(self, repositories):
        field = self.fields['repository']
        current = field.currentData() or ''
        field.clear()
        field.addItem('请选择 GitHub 仓库', '')
        for repository in repositories:
            field.addItem(repository, repository)
        if current and field.findData(current) == -1:
            field.addItem(current, current)
        field.setCurrentIndex(max(0, field.findData(current)))

    def collect(self):
        values = {}
        for key, field in self.fields.items():
            if isinstance(field, QCheckBox):
                values[key] = field.isChecked()
            elif isinstance(field, QSpinBox):
                values[key] = field.value()
            elif isinstance(field, QComboBox):
                values[key] = field.currentData()
            else:
                values[key] = field.text().strip()
        return replace(self.worker, **values)

    def dirty(self):
        return self.collect().to_dict() != self.worker.to_dict()
