from dataclasses import replace
from PySide6.QtCore import Signal, Qt
from PySide6.QtWidgets import QCheckBox, QComboBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QSpinBox, QWidget, QPlainTextEdit
from codex_dispatcher.domain.models import Worker
from codex_dispatcher.domain.notification_format import DEFAULT_NOTIFICATION_TEMPLATE
from codex_dispatcher.services.notification_service import build_notification


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
                self.repository_refresh = refresh = QPushButton('刷新仓库')
                refresh.clicked.connect(self.refresh_repositories)
                row = QHBoxLayout()
                row.addWidget(field, 1)
                row.addWidget(refresh)
                form.addRow(title, row)
                self.repository_status = QLabel()
                self.repository_status.setTextFormat(Qt.PlainText)
                self.repository_status.setWordWrap(True)
                self.repository_status.hide()
                form.addRow('', self.repository_status)
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
        self.fields['notification_template'] = template = QPlainTextEdit()
        template.setMinimumHeight(100)
        template.setMaximumHeight(140)
        template.textChanged.connect(self.update_preview)
        form.addRow('通知格式', template)
        hint = QLabel('必填变量：{issue_url}、{notification_id}\n可选：{repository}、{issue_number}、{source}')
        hint.setWordWrap(True)
        hint.setToolTip('修改影响后续新通知；已排队通知保留发现时的格式。')
        reset = QPushButton('恢复默认格式')
        reset.clicked.connect(lambda: template.setPlainText(DEFAULT_NOTIFICATION_TEMPLATE))
        row = QHBoxLayout()
        row.addWidget(hint, 1)
        row.addWidget(reset)
        form.addRow('', row)
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setMaximumHeight(100)
        form.addRow('发送预览\n（示例 #1）', self.preview)
        self.fields['repository'].currentIndexChanged.connect(self.update_preview)
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
            elif isinstance(field, QPlainTextEdit):
                field.setPlainText(value)
            else:
                field.setText(value)
        self.update_assignment_hint()
        self.update_preview()

    def update_preview(self):
        if not hasattr(self, 'preview'):
            return
        try:
            text = build_notification(self.fields['repository'].currentData() or 'owner/repository', 1,
                                      '00000000-0000-0000-0000-000000000000',
                                      template=self.fields['notification_template'].toPlainText())
        except ValueError as exc:
            text = '格式错误：' + str(exc)
        self.preview.setPlainText(text)

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
        self.repository_refresh.setEnabled(True)
        self.repository_status.hide()

    def repositories_loading(self):
        self.repository_refresh.setEnabled(False)
        self.repository_status.setStyleSheet('color: #68768a;')
        self.repository_status.setText('正在读取仓库列表…现有配置仍可编辑。')
        self.repository_status.show()

    def repository_error(self, error):
        self.repository_refresh.setEnabled(True)
        self.repository_status.setStyleSheet('color: #b42318;')
        self.repository_status.setText('仓库列表刷新失败，可继续编辑已有配置；点击“刷新仓库”重试。\n' + error[-220:])
        self.repository_status.setToolTip(error)
        self.repository_status.show()

    def collect(self):
        values = {}
        for key, field in self.fields.items():
            if isinstance(field, QCheckBox):
                values[key] = field.isChecked()
            elif isinstance(field, QSpinBox):
                values[key] = field.value()
            elif isinstance(field, QComboBox):
                values[key] = field.currentData()
            elif isinstance(field, QPlainTextEdit):
                values[key] = field.toPlainText().strip()
            else:
                values[key] = field.text().strip()
        return replace(self.worker, **values)

    def dirty(self):
        return self.collect().to_dict() != self.worker.to_dict()

    def dispose(self):
        # Combo/text fields can emit while Qt tears down their internal models.
        # Disable preview callbacks before any sibling widget is destroyed.
        for field in self.fields.values():
            field.blockSignals(True)
        self.preview.blockSignals(True)
        self.deleteLater()
