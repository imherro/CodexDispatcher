"""User-configured notification; external Issue text never enters the prompt."""
from codex_dispatcher.domain.models import normalize_repository
from codex_dispatcher.domain.notification_format import DEFAULT_NOTIFICATION_TEMPLATE, validate_notification_template


def build_notification(repository: str, number: int, notification_id: str, *, comment_id: int | None = None,
                       template: str = DEFAULT_NOTIFICATION_TEMPLATE) -> str:
    validate_notification_template(template)
    repository = normalize_repository(repository)
    if type(number) is not int or number < 1:
        raise ValueError('Issue 编号必须是正整数')
    # ID comes from our database, never GitHub text.
    from uuid import UUID
    notification_id = str(UUID(notification_id))
    if comment_id is not None and (type(comment_id) is not int or comment_id < 1):
        raise ValueError('评论编号必须是正整数')
    url = f'https://github.com/{repository}/issues/{number}'
    if comment_id is not None:
        url += f'#issuecomment-{comment_id}'
    source = '该条评论及所属 Issue' if comment_id is not None else '该 Issue'
    return template.format(notification_id=notification_id, issue_url=url, repository=repository,
                           issue_number=number, source=source)
