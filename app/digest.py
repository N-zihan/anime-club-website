"""每周邮件周报：番剧新闻摘要 + 赛事进度，供 Vercel Cron 每周一调用。"""
import logging

from .anime_news import get_anime_news
from .models import Contest, User
from .notify import _build_email_html
from .preferences import get_preferences
from .utils import cn_now

logger = logging.getLogger(__name__)


def _email_enabled(user):
    """尊重用户的「邮件通知」开关（偏好结构兼容顶层/notify 嵌套两种历史写法）。"""
    try:
        prefs = get_preferences(user) or {}
    except Exception:  # pragma: no cover - 偏好 JSON 异常时默认允许
        return True
    nested = prefs.get('notify') if isinstance(prefs.get('notify'), dict) else {}
    return bool(nested.get('email_enabled', prefs.get('email_enabled', True)))


def digest_recipients():
    """有邮箱且未关闭邮件通知的社员邮箱列表。"""
    users = User.query.filter(User.email.isnot(None)).all()
    emails = []
    for user in users:
        if user.email and _email_enabled(user):
            emails.append(user.email)
    return emails


def build_digest(now=None):
    """生成周报 (subject, html, text)。数据源拿不到时降级为提示文案。"""
    now = now or cn_now()
    try:
        articles, _meta = get_anime_news(limit=5)
    except Exception as exc:  # 新闻源挂了不影响周报发送
        logger.warning('周报新闻获取失败: %s', exc)
        articles = []

    lines = []
    if articles:
        lines.append('【本周番剧新闻】')
        for i, article in enumerate(articles, 1):
            title = article.get('title_zh') or article.get('title') or '(无标题)'
            link = article.get('link') or article.get('url') or ''
            lines.append(f'{i}. {title}')
            if link:
                lines.append(f'   {link}')
        lines.append('')

    contests = (Contest.query
                .filter(Contest.status.in_(['open', 'group_stage', 'knockout']))
                .order_by(Contest.created_at.desc()).all())
    if contests:
        lines.append('【赛事进行中】')
        for contest in contests:
            lines.append(f'· {contest.title}')
        lines.append('')

    if not lines:
        lines.append('本周没有新内容，去站里逛逛吧～')

    content = '\n'.join(lines).strip()
    subject = '【动漫社】本周番剧新闻 & 赛事进度'
    html = _build_email_html('动漫社周报 · ' + now.strftime('%m-%d'), content)
    return subject, html, content


def send_weekly_digest():
    """给符合条件的社员发周报，返回统计（给 cron 接口返回用）。"""
    from .auth import send_email_batch  # 延迟导入，避免 auth ↔ digest 循环

    subject, html, _text = build_digest()
    emails = digest_recipients()
    if not emails:
        return {'recipients': 0, 'sent': 0}
    sent = send_email_batch(emails, subject, html, is_html=True)
    return {'recipients': len(emails), 'sent': sent}
