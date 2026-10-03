from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
from datetime import datetime, timezone
from pathlib import Path
import os
import re
import uuid
from urllib.parse import urlparse
from .notification_format import DEFAULT_NOTIFICATION_TEMPLATE, validate_notification_template


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def same_path(a: str, b: str) -> bool:
    if not a or not b:
        return False
    return os.path.normcase(os.path.realpath(a)) == os.path.normcase(os.path.realpath(b))


def normalize_repository(value: str) -> str:
    value = value.strip().rstrip('/')
    if '://' in value:
        parsed = urlparse(value)
        if parsed.scheme != 'https' or parsed.hostname != 'github.com' or parsed.query or parsed.fragment:
            raise ValueError('仓库必须是 owner/repository 或 https://github.com/owner/repository')
        value = parsed.path.strip('/')
    if value.endswith('.git'):
        value = value[:-4]
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9_.-]+', value):
        raise ValueError('仓库格式不正确：请填写 owner/repository')
    if value.split('/')[1] in ('.', '..'):
        raise ValueError('仓库名称无效')
    return value


def normalize_mention(value: str) -> str:
    value = value.strip().removeprefix('@')
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}', value):
        raise ValueError('@ 名称只能包含字母、数字、下划线和连字符，长度 1–64')
    return value


def contains_mention(text: str, value: str) -> bool:
    name = normalize_mention(value)
    return re.search(r'(?<![A-Za-z0-9_@/.-])@?' + re.escape(name) + r'(?![A-Za-z0-9_@-])',
                     text or '', re.IGNORECASE) is not None


def is_agent_report(text: str, value: str) -> bool:
    """Recognize an explicit self-signature, not arbitrary completion prose."""
    name = normalize_mention(value)
    return re.match(r"\s*(?:我是\s*|I\s+am\s+|I'm\s+)[*_`]*@?" + re.escape(name)
                    + r'(?![A-Za-z0-9_@-])', text or '', re.IGNORECASE) is not None


@dataclass
class Worker:
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    name: str = ''
    enabled: bool = True
    repository: str = ''
    assignment_mode: str = 'mention'
    assignment_value: str = ''
    target_project: str = ''
    target_thread_id: str = ''
    target_thread_name: str = ''
    ignored_labels: list[str] = field(default_factory=lambda: ['agent:running', 'agent:done', 'agent:blocked'])
    poll_interval: int = 5
    notification_template: str = DEFAULT_NOTIFICATION_TEMPLATE
    created_at: str = field(default_factory=now)
    updated_at: str = field(default_factory=now)

    def validate(self):
        self.repository = normalize_repository(self.repository)
        self.target_project = str(Path(self.target_project).resolve()) if self.target_project.strip() else ''
        for key in ('name', 'assignment_value', 'target_thread_id'):
            setattr(self, key, getattr(self, key).strip())
            if not getattr(self, key):
                raise ValueError(f'{key} 不能为空')
        if self.assignment_mode not in ('mention', 'label', 'assignee'):
            raise ValueError('分配模式无效')
        if self.assignment_mode == 'mention':
            self.assignment_value = normalize_mention(self.assignment_value)
        if not 1 <= self.poll_interval <= 60:
            raise ValueError('检查间隔必须为 1–60 分钟')
        validate_notification_template(self.notification_template)
        if not self.target_project or not Path(self.target_project).is_dir():
            raise ValueError('目标项目目录不存在')
        self.updated_at = now()

    @classmethod
    def from_dict(cls, data):
        # Load v0.1 databases and queued snapshots without reviving removed features.
        keys = {f.name for f in fields(cls)}
        return cls(**{key: value for key, value in data.items() if key in keys})

    def to_dict(self):
        return asdict(self)


@dataclass
class Issue:
    repository: str
    number: int
    title: str
    body: str
    url: str
    updated_at: str
    labels: list[str] = field(default_factory=list)
    assignees: list[str] = field(default_factory=list)
    comments: list[dict] = field(default_factory=list)
    state: str = 'OPEN'
    notification_key: str = 'issue'
    comment_id: int | None = None

    @classmethod
    def from_github(cls, repository, data, *, include_content=False):
        return cls(repository, data['number'], data.get('title') or '' if include_content else '',
                   data.get('body') or '' if include_content else '',
                   data['url'], data['updatedAt'],
                   [x['name'] for x in data.get('labels', [])],
                   [x['login'] for x in data.get('assignees', [])],
                   [], data.get('state', 'OPEN'))

    def metadata(self):
        return {key: value for key, value in self.to_dict().items()
                if key not in ('title', 'body', 'comments')}

    def matches(self, worker: Worker) -> bool:
        if self.repository.casefold() != worker.repository.casefold() or self.state.upper() != 'OPEN':
            return False
        if {x.casefold() for x in self.labels} & {x.casefold() for x in worker.ignored_labels}:
            return False
        if worker.assignment_mode == 'mention':
            if self.comment_id is not None and is_agent_report(self.body, worker.assignment_value):
                return False
            return contains_mention(self.title + '\n' + self.body, worker.assignment_value)
        values = self.labels if worker.assignment_mode == 'label' else self.assignees
        return worker.assignment_value.casefold() in {x.casefold() for x in values}

    def to_dict(self):
        return asdict(self)


@dataclass
class ThreadInfo:
    id: str
    cwd: str
    name: str = ''
    preview: str = ''
    updated_at: int | None = None
    created_at: int | None = None
    model: str | None = None
    source: str | dict | None = None
    status: str = 'unknown'

    @classmethod
    def from_wire(cls, data):
        return cls(id=data['id'], cwd=data.get('cwd') or '', name=data.get('name') or '',
                   preview=data.get('preview') or '', updated_at=data.get('updatedAt'),
                   created_at=data.get('createdAt'), model=data.get('model'), source=data.get('source'),
                   status=data.get('status', {}).get('type', 'unknown'))


class DispatchError(Exception):
    pass


class ThreadBusy(DispatchError):
    """Known busy/conflict before any new task was accepted."""
    def __init__(self, message, *, keep_queued=False):
        super().__init__(message)
        self.keep_queued = keep_queued


class RecoveryRequired(DispatchError):
    """Submission outcome is unknown: never retry automatically."""


class WorkerPaused(DispatchError):
    pass
