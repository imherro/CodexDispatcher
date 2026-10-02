from dataclasses import replace

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFileDialog, QFormLayout, QHBoxLayout,
                              QLabel, QLineEdit, QPlainTextEdit, QPushButton, QScrollArea,
                              QSpinBox, QTabWidget, QVBoxLayout, QWidget)

from codex_dispatcher.domain.models import DEFAULT_TEMPLATE, Worker


class WorkerEditor(QWidget):
    refresh_threads = Signal(str)
    verify_thread = Signal()
    test_message = Signal()
    test_github = Signal()
    inspect_project = Signal(str)
    refresh_models = Signal()

    def __init__(self):
        super().__init__()
        self.worker = Worker()
        self.fields = {}
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs)
        basic, basic_form = self.page('基本配置')
        self.line(basic_form, '配置名称', 'name', '例如：项目名称 / Worker 名称')
        self.line(basic_form, 'Worker 名称', 'worker_name', '例如：codex-1070-1（不要求是 GitHub 用户名）')
        self.check(basic_form, '启用 Worker', 'enabled')
        gh = QPushButton('测试 GitHub')
        gh.clicked.connect(self.test_github)
        self.line(basic_form, 'GitHub 仓库', 'repository', 'owner/repository 或 GitHub 仓库 URL', gh)
        self.fields['assignment_mode'] = QComboBox()
        self.fields['assignment_mode'].addItem('Label · 推荐虚拟 Worker', 'label')
        self.fields['assignment_mode'].addItem('Assignee · 真实 GitHub 用户', 'assignee')
        basic_form.addRow('分配模式', self.fields['assignment_mode'])
        self.line(basic_form, '分配值', 'assignment_value', '例如：agent:codex-1070-1 或真实 GitHub 用户名')
        choose = QPushButton('选择文件夹…')
        choose.clicked.connect(self.choose_project)
        self.line(basic_form, '目标项目', 'target_project', '选择本地项目文件夹', choose)
        self.project_info = QLabel('选择目录后可查看 Git remote、branch 和会话数量。')
        self.project_info.setWordWrap(True)
        self.project_info.setObjectName('hint')
        self.line(basic_form, 'Codex Thread ID', 'target_thread_id', '刷新会话选择，或手工粘贴 Thread ID')
        actions = QWidget()
        action_layout = QHBoxLayout(actions)
        action_layout.setContentsMargins(0, 0, 0, 0)
        refresh, verify, copy = QPushButton('获取此项目的 Codex 会话'), QPushButton('验证会话'), QPushButton('复制 ID')
        refresh.clicked.connect(lambda: self.refresh_threads.emit(self.fields['target_project'].text()))
        verify.clicked.connect(self.verify_thread)
        copy.clicked.connect(lambda: self.copy_thread())
        for button in (refresh, verify, copy):
            action_layout.addWidget(button)
        basic_form.addRow('', actions)
        interval = QSpinBox()
        interval.setRange(1, 60)
        interval.setSuffix(' 分钟')
        self.fields['poll_interval'] = interval
        basic_form.addRow('检查间隔', interval)
        self.authorization = QLabel('保存并开启监视后，符合分配规则的新 Issue 会自动送入指定 Codex 会话。\n'
                                    '监视期间请让 Dispatcher 独占此会话；人工操作前停止监视并等待当前任务完成。')
        self.authorization.setObjectName('notice')
        self.authorization.setWordWrap(True)
        basic_form.addRow('', self.authorization)

        advanced, advanced_form = self.page('过滤与高级设置')
        self.line(advanced_form, '备注', 'description', '可选')
        advanced_form.addRow('项目信息', self.project_info)
        self.line(advanced_form, '忽略 labels', 'ignored_labels', '以逗号分隔')
        self.check(advanced_form, 'Issue 更新后允许重新派送', 'redispatch_updated')
        self.check(advanced_form, '允许忽略项目路径不匹配', 'allow_mismatch')
        self.check(advanced_form, '使用廉价模型整理任务（默认关闭）', 'dispatcher_enabled')
        model = QComboBox()
        model.setEditable(True)
        model.setPlaceholderText('刷新模型，或填写可用模型名称')
        self.fields['dispatcher_model'] = model
        advanced_form.addRow('整理模型', model)
        self.fields['dispatcher_reasoning'] = QComboBox()
        self.fields['dispatcher_reasoning'].addItems(['minimal', 'low', 'medium', 'high'])
        advanced_form.addRow('整理 reasoning', self.fields['dispatcher_reasoning'])
        models = QPushButton('动态刷新模型列表')
        models.clicked.connect(self.refresh_models)
        advanced_form.addRow('', models)
        hint = QLabel('整理会话是临时只读会话，关闭 shell / MCP 等工具入口，不访问目标项目。\n'
                      '权限限制或模型不可用时会明确失败，可关闭整理继续派送。')
        hint.setWordWrap(True)
        advanced_form.addRow('', hint)
        self.check(advanced_form, 'Override target model · 覆盖目标模型', 'override_target_model')
        self.line(advanced_form, '目标模型', 'target_model', '默认继承目标会话；只有打开覆盖开关才生效')
        send_test = QPushButton('发送测试消息…（需要二次确认）')
        send_test.clicked.connect(self.test_message)
        advanced_form.addRow('', send_test)

        template_page = QWidget()
        template_layout = QVBoxLayout(template_page)
        template_layout.addWidget(QLabel('模板变量：worker_name、repository、issue_number、issue_title、issue_url、issue_body、\n'
                                        'issue_comments、task_summary、requirements、acceptance_criteria、dispatch_time；使用 {{变量}}。'))
        prompt = QPlainTextEdit()
        self.fields['prompt_template'] = prompt
        template_layout.addWidget(prompt)
        reset = QPushButton('恢复默认模板')
        reset.clicked.connect(lambda: prompt.setPlainText(DEFAULT_TEMPLATE))
        template_layout.addWidget(reset)
        self.tabs.addTab(template_page, '任务模板')
        self.load(self.worker)

    def page(self, title):
        container = QWidget()
        form = QFormLayout(container)
        form.setContentsMargins(18, 16, 18, 18)
        form.setVerticalSpacing(8)
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setWidget(container)
        self.tabs.addTab(scroll, title)
        return container, form

    def line(self, form, title, key, placeholder, button=None):
        field = QLineEdit()
        field.setPlaceholderText(placeholder)
        self.fields[key] = field
        if button:
            container = QWidget()
            layout = QHBoxLayout(container)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.addWidget(field, 1)
            layout.addWidget(button)
            form.addRow(title, container)
        else:
            form.addRow(title, field)

    def check(self, form, title, key):
        field = QCheckBox(title)
        self.fields[key] = field
        form.addRow('', field)

    def load(self, worker):
        self.worker = replace(worker)
        for key, field in self.fields.items():
            value = getattr(worker, key)
            if isinstance(field, QCheckBox):
                field.setChecked(value)
            elif isinstance(field, QSpinBox):
                field.setValue(value)
            elif isinstance(field, QPlainTextEdit):
                field.setPlainText(value)
            elif isinstance(field, QComboBox):
                if key == 'assignment_mode':
                    field.setCurrentIndex(field.findData(value))
                else:
                    field.setCurrentText(value)
            else:
                field.setText(', '.join(value) if key == 'ignored_labels' else value)

    def collect(self):
        values = {}
        for key, field in self.fields.items():
            if isinstance(field, QCheckBox):
                value = field.isChecked()
            elif isinstance(field, QSpinBox):
                value = field.value()
            elif isinstance(field, QPlainTextEdit):
                value = field.toPlainText()
            elif isinstance(field, QComboBox):
                value = field.currentData() if key == 'assignment_mode' else field.currentText()
            else:
                value = field.text()
            if key == 'ignored_labels':
                value = [x.strip() for x in value.split(',') if x.strip()]
            values[key] = value
        return replace(self.worker, **values)

    def dirty(self):
        return self.collect().to_dict() != self.worker.to_dict()

    def choose_project(self):
        path = QFileDialog.getExistingDirectory(self, '选择目标本地项目', self.fields['target_project'].text())
        if path:
            self.fields['target_project'].setText(path)
            self.fields['target_thread_id'].clear()
            self.inspect_project.emit(path)

    def set_models(self, models):
        field = self.fields['dispatcher_model']
        current = field.currentText()
        field.clear()
        field.addItems([model.get('model', model.get('id', '')) for model in models])
        field.setCurrentText(current)

    def copy_thread(self):
        from PySide6.QtWidgets import QApplication
        QApplication.clipboard().setText(self.fields['target_thread_id'].text())
