"""用户偏好（账号级设置）的读写、默认值与容错。

存储：users.preferences（TEXT，JSON 文本）。读取时任何异常都回落到默认值，
因此空值、老数据、脏 JSON 都不会让页面报错；写入前统一做规范化，
只接受白名单内的取值。

阶段 B 只包含「通知」一组；后续（语言/翻译等）新增分组时，
只要往 DEFAULT_PREFERENCES 里加键即可，旧数据会自动补全默认值。
"""
import json

# 站内通知真实会产生的类型（contest 目前没有任何生产者，先不暴露开关）
NOTIFY_TYPES = ('reply', 'audit', 'system')

NOTIFY_LABELS = {
    'reply': '回复提醒',
    'audit': '审核结果',
    'system': '系统通知',
}

# 角标轮询间隔可选值（秒）
BADGE_POLL_CHOICES = (60, 120, 300)

DEFAULT_PREFERENCES = {
    'notify': {
        'muted_types': [],          # 被静音的通知类型：不入列表、不计入红点
        'email_enabled': True,      # 是否允许把通知同时发到邮箱
        'badge_enabled': True,      # 是否轮询未读红点
        'badge_poll_seconds': 60,   # 红点轮询间隔
    },
}


def _defaults():
    """返回一份新的默认偏好（含嵌套 dict 的深拷贝）"""
    return json.loads(json.dumps(DEFAULT_PREFERENCES))


def _deep_merge(base, override):
    """把 override 合并进 base：dict 递归合并，其余直接覆盖"""
    result = dict(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _as_bool(value, default):
    """把表单/JSON 里的值稳妥地转成布尔（'false'、'0' 也当 False）"""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() not in ('', '0', 'false', 'no', 'off')
    return default


def parse_preferences(raw):
    """把数据库里的 JSON 文本解析成完整偏好字典（容错，永不抛异常）"""
    prefs = _defaults()
    if not raw:
        return prefs
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return prefs
    if not isinstance(data, dict):
        return prefs
    return _deep_merge(prefs, data)


def normalize_preferences(data):
    """规范化偏好：保留未知键，但强制校验已知键的取值范围"""
    prefs = _deep_merge(_defaults(), data if isinstance(data, dict) else {})
    notify = prefs.get('notify') if isinstance(prefs.get('notify'), dict) else {}

    muted = notify.get('muted_types')
    if isinstance(muted, str):
        muted = [muted]
    if not isinstance(muted, (list, tuple, set)):
        muted = []
    muted_set = {str(t) for t in muted}
    muted_types = [t for t in NOTIFY_TYPES if t in muted_set]

    poll = notify.get('badge_poll_seconds')
    try:
        poll = int(poll)
    except (TypeError, ValueError):
        poll = DEFAULT_PREFERENCES['notify']['badge_poll_seconds']
    if poll not in BADGE_POLL_CHOICES:
        poll = DEFAULT_PREFERENCES['notify']['badge_poll_seconds']

    defaults = DEFAULT_PREFERENCES['notify']
    prefs['notify'] = {
        'muted_types': muted_types,
        'email_enabled': _as_bool(notify.get('email_enabled'), defaults['email_enabled']),
        'badge_enabled': _as_bool(notify.get('badge_enabled'), defaults['badge_enabled']),
        'badge_poll_seconds': poll,
    }
    return prefs


def get_preferences(user):
    """读取用户偏好（user 为 None 时返回默认值）"""
    if user is None:
        return _defaults()
    return normalize_preferences(parse_preferences(getattr(user, 'preferences', None)))


def save_preferences(user, data):
    """规范化并写回用户偏好（不发 commit，由调用方决定事务边界）"""
    normalized = normalize_preferences(data)
    user.preferences = json.dumps(normalized, ensure_ascii=False)
    return normalized


def muted_types(user):
    """当前用户静音的通知类型列表"""
    return get_preferences(user)['notify']['muted_types']


def should_send_mail(user, notify_type):
    """该用户对该类型通知是否还应该收到邮件"""
    notify = get_preferences(user)['notify']
    if not notify.get('email_enabled', True):
        return False
    return notify_type not in notify.get('muted_types', [])
