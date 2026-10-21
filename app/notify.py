"""
动漫社官网 · 通知模块
====================================
提供站内通知 + 邮件通知。
"""
import os

from .models import db, Notification, User
from .preferences import should_send_mail
from .auth import send_email, send_email_batch

club_name = os.getenv('CLUB_NAME', '动漫社')


def _build_email_html(title: str, content: str, link: str = None) -> str:
    """生成统一风格的 HTML 邮件正文"""
    content_html = (content or '').replace('\r\n', '<br>').replace('\n', '<br>')
    link_html = ''
    if link:
        link_html = f"""
        <div style="margin:24px 0; text-align:center;">
            <a href="{link}" style="display:inline-block; padding:12px 32px; background:#1e2a3a; color:#ffffff; text-decoration:none; border-radius:40px; font-weight:600;">查看详情</a>
        </div>
        """

    return f"""
        <!DOCTYPE html>
        <html>
        <head><meta charset="UTF-8"></head>
        <body style="margin:0; padding:0; background:#f0f8ff; font-family:'Helvetica Neue',Arial,sans-serif;">
            <div style="max-width:520px; margin:40px auto; background:#ffffff; border-radius:16px; overflow:hidden; box-shadow:0 4px 16px rgba(0,0,0,0.06);">
                <div style="background:#1e2a3a; padding:24px 32px;">
                    <h1 style="margin:0; color:#ffffff; font-size:1.2rem; font-weight:600;">{club_name}</h1>
                    <p style="margin:6px 0 0 0; color:#94a3b8; font-size:0.8rem;">以热爱为名 · 共创二次元家园</p>
                </div>
                <div style="padding:32px;">
                    <h2 style="margin:0 0 16px 0; color:#1e2a3a; font-size:1.1rem;">{title}</h2>
                    <div style="color:#334155; font-size:0.95rem; line-height:1.7;">
                        {content_html}
                    </div>
                    {link_html}
                </div>
                <div style="background:#f8fafc; padding:16px 32px; text-align:center;">
                    <p style="margin:0; color:#94a3b8; font-size:0.75rem;">
                        此邮件由系统自动发送，请勿回复
                    </p>
                </div>
            </div>
        </body>
        </html>
        """


def _absolute_link(link: str) -> str:
    """把相对路径转成绝对 URL"""
    if not link:
        return None
    if link.startswith('http'):
        return link
    base = os.getenv('SITE_URL', 'https://www.nanyi-anime-club.top')
    return base.rstrip('/') + link


def notify(user_id: int, title: str, content: str = '', notify_type: str = 'system',
           link: str = None, send_mail: bool = True):
    """给单个用户发通知（站内 + 邮件）"""
    # 1. 站内通知
    n = Notification(
        user_id=user_id,
        title=title,
        content=content,
        type=notify_type,
        link=link,
    )
    db.session.add(n)

    # 2. 邮件通知（尊重用户的通知偏好：关闭邮件或静音该类型时不发）
    if send_mail:
        user = db.session.get(User, user_id)
        if user and user.email and should_send_mail(user, notify_type):
            html = _build_email_html(title, content or '你有一条新通知', _absolute_link(link))
            try:
                send_email(user.email, f'【{club_name}】{title}', html, is_html=True)
            except Exception as e:
                print(f'邮件通知发送失败: {e}')


def notify_many(user_ids: list, title: str, content: str = '', notify_type: str = 'system',
                link: str = None, send_mail: bool = True):
    """批量发通知"""
    for uid in user_ids:
        notify(uid, title, content, notify_type, link, send_mail=send_mail)


def notify_all(title: str, content: str = '', notify_type: str = 'system',
               link: str = None, send_mail: bool = True):
    """给所有用户发通知（站内 + 邮件批量）"""
    users = db.session.query(User).all()

    # 1. 站内通知：逐个插入
    for u in users:
        db.session.add(Notification(
            user_id=u.id,
            title=title,
            content=content,
            type=notify_type,
            link=link,
        ))

    # 2. 邮件通知：一次连接批量发送（按各用户的静音/邮件偏好过滤）
    if send_mail:
        emails = [u.email for u in users if u.email and should_send_mail(u, notify_type)]
        if emails:
            html = _build_email_html(title, content or '你有一条新通知', _absolute_link(link))
            send_email_batch(emails, f'【{club_name}】{title}', html, is_html=True)
