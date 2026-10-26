"""每周邮件周报：番剧新闻摘要 + 赛事阶段/截止时间，带一键退订。

由 Vercel Cron 每周一调用（/api/cron/weekly-digest）。每封信带用户专属退订链接
（签名 token，30 天有效），点一下即关闭「邮件通知」，不用登录。
"""
import html
import logging
import time
from datetime import timedelta

from flask import current_app
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from .anime_news import get_anime_news
from .contest_engine import calc_phase, calc_stage_times
from .models import Contest, DigestSend, User, db
from .notify import _build_email_html
from .preferences import get_preferences, save_preferences
from .utils import cn_now

logger = logging.getLogger(__name__)

UNSUB_SALT = 'digest-unsubscribe'
UNSUB_MAX_AGE = 30 * 24 * 3600   # 30 天

# 邮件里展示的赛事阶段中文名
PHASE_LABELS = {
    'nomination': '提名期', 'review': '审核期', 'qualifying': '海选投票',
    'qualifying_result': '海选公示',
    'group_round_1': '小组赛第1轮', 'group_round_1_result': '小组赛第1轮公示',
    'group_round_2': '小组赛第2轮', 'group_round_2_result': '小组赛第2轮公示',
    'group_round_3': '小组赛第3轮', 'group_round_3_result': '小组赛第3轮公示',
    'knockout_16': '淘汰赛16强', 'knockout_16_result': '16强公示',
    'knockout_8': '淘汰赛8强', 'knockout_8_result': '8强公示',
    'knockout_4': '淘汰赛4强', 'knockout_4_result': '4强公示',
    'final_vote': '决赛投票', 'final_result': '最终结果', 'closed': '已结束',
}

# 截止时间展示顺序（key -> 中文名）
DEADLINE_LABELS = (
    ('nomination_end', '提名截止'),
    ('review_end', '审核截止'),
    ('qualifying_vote_end', '海选投票截止'),
    ('qualifying_end', '海选公示结束'),
    ('group_round_1_end', '小组赛第1轮截止'),
    ('group_round_2_end', '第2轮截止'),
    ('group_round_3_end', '第3轮截止'),
    ('knockout_16_end', '16强截止'),
    ('knockout_8_end', '8强截止'),
    ('knockout_4_end', '4强截止'),
    ('final_vote_end', '决赛投票截止'),
    ('final_result_end', '赛事结束'),
)


# ---------- 退订 token ----------
def _serializer():
    return URLSafeTimedSerializer(current_app.secret_key, salt=UNSUB_SALT)


def make_unsubscribe_token(user_id):
    return _serializer().dumps({'uid': user_id})


def parse_unsubscribe_token(token, max_age=UNSUB_MAX_AGE):
    """返回 user_id；token 无效/过期返回 None。"""
    try:
        data = _serializer().loads(token, max_age=max_age)
    except (BadSignature, SignatureExpired):
        return None
    if not isinstance(data, dict):
        return None
    try:
        return int(data.get('uid'))
    except (TypeError, ValueError):
        return None


# ---------- 收件人 / 内容 ----------
def _email_enabled(user):
    """尊重用户的「邮件通知」开关（兼容顶层/notify 嵌套两种历史写法）。"""
    try:
        prefs = get_preferences(user) or {}
    except Exception:  # pragma: no cover - 偏好 JSON 异常时默认允许
        return True
    nested = prefs.get('notify') if isinstance(prefs.get('notify'), dict) else {}
    return bool(nested.get('email_enabled', prefs.get('email_enabled', True)))


def digest_recipients():
    """有邮箱且未关闭邮件通知的社员列表。"""
    users = User.query.filter(User.email.isnot(None)).all()
    return [u for u in users if u.email and _email_enabled(u)]


def contest_progress(contest, now):
    """返回 (阶段中文名, 下一个截止说明, 截止时间字符串)。"""
    if not contest.open_at:
        return PHASE_LABELS.get(contest.status, contest.status), '', ''
    open_at = contest.open_at
    if getattr(open_at, 'tzinfo', None) is not None:
        open_at = open_at.replace(tzinfo=None)
    times = calc_stage_times(open_at)
    try:
        phase = calc_phase(contest, now, times)
    except Exception:  # pragma: no cover - 异常时退回状态
        phase = contest.status
    upcoming = [(value, label) for key, label in DEADLINE_LABELS
                for value in [times.get(key)] if value and value > now]
    deadline_label, deadline_str = '', ''
    if upcoming:
        deadline = min(upcoming, key=lambda item: item[0])
        deadline_label = f'{deadline[1]}：{deadline[0].strftime("%m-%d %H:%M")}'
        deadline_str = deadline[0].strftime('%Y-%m-%d %H:%M')
    return PHASE_LABELS.get(phase, phase or contest.status), deadline_label, deadline_str


def _excerpt(article, limit=90):
    text = (article.get('excerpt_zh') or article.get('excerpt') or '').strip()
    text = ' '.join(text.split())
    return text[:limit] + ('…' if len(text) > limit else '')


def _news_html(article):
    title = article.get('title_zh') or article.get('title') or '(无标题)'
    link = article.get('link') or article.get('url') or ''
    excerpt = _excerpt(article)
    image = article.get('image') or ''
    safe_title = html.escape(title)
    safe_excerpt = html.escape(excerpt)
    title_html = f'<a href="{html.escape(link)}" style="color:#1e2a3a;">{safe_title}</a>' if link else safe_title
    image_html = (f'<img src="{html.escape(image)}" alt="" width="64" height="64" '
                  'style="width:64px;height:64px;object-fit:cover;border-radius:8px;margin-right:12px;vertical-align:top;">'
                  if image else '')
    return (
        '<div style="display:block; margin:0 0 14px 0;">'
        f'{image_html}<div style="display:inline-block; width:86%; vertical-align:top;">'
        f'<div style="font-weight:600;">{title_html}</div>'
        + (f'<div style="color:#64748b; font-size:0.85rem; margin-top:2px;">{safe_excerpt}</div>' if excerpt else '')
        + '</div></div>'
    )


def build_digest(now=None, unsubscribe_url=None):
    """生成周报 (subject, html, text)。数据源拿不到时降级为提示文案。"""
    now = now or cn_now()
    try:
        articles, _meta = get_anime_news(limit=5)
    except Exception as exc:  # 新闻源挂了不影响周报发送
        logger.warning('周报新闻获取失败: %s', exc)
        articles = []

    html_parts, text_lines = [], []
    if articles:
        html_parts.append('<h3 style="margin:0 0 12px 0; color:#1e2a3a;">📰 本周番剧新闻</h3>')
        text_lines.append('【本周番剧新闻】')
        for i, article in enumerate(articles, 1):
            title = article.get('title_zh') or article.get('title') or '(无标题)'
            link = article.get('link') or article.get('url') or ''
            excerpt = _excerpt(article)
            html_parts.append(_news_html(article))
            text_lines.append(f'{i}. {title}')
            if excerpt:
                text_lines.append(f'   {excerpt}')
            if link:
                text_lines.append(f'   {link}')
        html_parts.append('<div style="margin:18px 0;"></div>')
        text_lines.append('')

    contests = (Contest.query
                .filter(Contest.status.in_(['open', 'group_stage', 'knockout']))
                .order_by(Contest.created_at.desc()).all())
    if contests:
        html_parts.append('<h3 style="margin:0 0 12px 0; color:#1e2a3a;">🏆 赛事进行中</h3>')
        text_lines.append('【赛事进行中】')
        for contest in contests:
            phase, deadline_label, _deadline = contest_progress(contest, now)
            safe_title = html.escape(contest.title)
            safe_phase = html.escape(phase or '')
            safe_deadline = html.escape(deadline_label or '')
            html_parts.append(
                f'<div style="margin:0 0 10px 0;"><strong>{safe_title}</strong>'
                f'<div style="color:#64748b; font-size:0.85rem;">当前：{safe_phase}'
                + (f' · {safe_deadline}' if safe_deadline else '')
                + '</div></div>')
            text_lines.append(f'· {contest.title}（当前：{phase}'
                              + (f'，{deadline_label}' if deadline_label else '') + '）')
        html_parts.append('<div style="margin:18px 0;"></div>')
        text_lines.append('')

    if not html_parts:
        html_parts.append('<p style="color:#64748b;">本周没有新内容，去站里逛逛吧～</p>')
        text_lines.append('本周没有新内容，去站里逛逛吧～')

    if unsubscribe_url:
        safe_url = html.escape(unsubscribe_url)
        html_parts.append(
            '<div style="margin-top:22px; padding-top:14px; border-top:1px solid #e2e8f0; '
            'color:#94a3b8; font-size:0.78rem;">'
            '不想再收周报？<a href="' + safe_url + '" style="color:#3f7fd0;">点此一键退订</a>'
            '（只关闭邮件通知，站内消息不受影响）</div>')
        text_lines.append('')
        text_lines.append(f'不想再收周报？点此退订：{unsubscribe_url}')

    content = '\n'.join(text_lines).strip()
    subject = '【动漫社】本周番剧新闻 & 赛事进度'
    html_body = _build_email_html('动漫社周报 · ' + now.strftime('%m-%d'),
                                  '\n'.join(html_parts))
    return subject, html_body, content


def _week_key(now):
    """当周周一的日期字符串，作为「同一周只发一次」的幂等键。"""
    return (now - timedelta(days=now.weekday())).strftime('%Y-%m-%d')


def _already_sent_user_ids(week):
    """本周已经发过的 user_id 集合；表还没建（没跑 init.py）时退化为空集合。"""
    try:
        rows = db.session.query(DigestSend.user_id).filter(DigestSend.week == week).all()
    except Exception as exc:  # pragma: no cover - 部署早期/表缺失时的兜底
        logger.warning('读取周报发送记录失败（digest_sends 表可能还没建）: %s', exc)
        db.session.rollback()
        return set()
    return {row[0] for row in rows}


def send_weekly_digest(base_url='', batch=None, time_budget=45):
    """给本周还没收到的社员发周报（单连接批量 + 断点续发），返回统计。

    Vercel 函数最长 60 秒。旧实现给每个收件人单独新建 SMTP 连接，100+ 人会在
    60 秒内被截断，所以出现「周报只发了一部分人」。现在：
    - 所有邮件共用一条 SMTP 连接（auth.send_html_batch）；
    - 每次最多 batch 封（默认 200）、最多跑 time_budget 秒（默认 45），超了就停；
    - 发送结果写 DigestSend（user_id + 当周周一），再次调用自动跳过已发的人。
    """
    from .auth import send_html_batch  # 延迟导入，避免 auth ↔ digest 循环

    now = cn_now()
    week = _week_key(now)
    recipients = [user for user in digest_recipients() if user.email]
    already = _already_sent_user_ids(week)
    pending = [user for user in recipients if user.id not in already]
    limit = max(1, int(batch or 200))
    pending = pending[:limit]

    started = time.monotonic()
    sent = failed = cursor = 0
    while cursor < len(pending):
        if time.monotonic() - started > time_budget:
            break
        chunk = pending[cursor:cursor + 10]
        cursor += len(chunk)

        payload = []
        for user in chunk:
            url = ''
            if base_url:
                token = make_unsubscribe_token(user.id)
                url = f'{base_url.rstrip("/")}/digest/unsubscribe/{token}'
            subject, body, _text = build_digest(now=now, unsubscribe_url=url or None)
            payload.append((user.email, subject, body))

        ok_emails = set(send_html_batch(payload))
        for user in chunk:
            ok = user.email in ok_emails
            if ok:
                sent += 1
            else:
                failed += 1
            db.session.add(DigestSend(user_id=user.id, week=week,
                                      email=user.email, ok=ok))
        try:
            db.session.commit()
        except Exception as exc:  # pragma: no cover - 记录失败不影响已发出的邮件
            logger.warning('记录周报发送结果失败: %s', exc)
            db.session.rollback()

    remaining = max(0, len([u for u in recipients if u.id not in already]) - cursor)
    return {
        'recipients': len(recipients),
        'sent': sent,
        'failed': failed,
        'remaining': remaining,
        'week': week,
        'done': remaining == 0,
    }
