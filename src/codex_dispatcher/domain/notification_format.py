from string import Formatter

DEFAULT_NOTIFICATION_TEMPLATE = (
    '[Codex Dispatcher · {notification_id}]\n'
    '有分配给你的待办，请自行读取 #{issue_number} issue  {source}，执行、验证并及时回复issue。\n'
    '参考网址：{issue_url}'
)
TEMPLATE_FIELDS = {'notification_id', 'issue_url', 'repository', 'issue_number', 'source'}


def validate_notification_template(template: str):
    if not isinstance(template, str) or not template.strip() or len(template) > 10000:
        raise ValueError('通知格式不能为空，长度不能超过 10000 字符')
    try:
        parsed = list(Formatter().parse(template))
    except ValueError as exc:
        raise ValueError('通知格式中的花括号不完整；普通花括号请写成 {{ 或 }}') from exc
    used = set()
    for _, name, spec, conversion in parsed:
        if name is None:
            continue
        if name not in TEMPLATE_FIELDS or spec or conversion:
            raise ValueError('通知格式包含不支持的变量：{' + name + '}')
        used.add(name)
    if not {'issue_url', 'notification_id'} <= used:
        raise ValueError('通知格式必须包含 {issue_url} 和 {notification_id}')
