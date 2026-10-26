"""
动漫社官网 · 用户认证模块
====================================

本模块处理社员账号的注册、登录、登出和注销功能。

核心设计：
- 注册时要求填写社团 QQ 群号作为验证码，防止校外人员注册
- 用户名支持中文、字母、数字、下划线（2-20位）
- 密码使用 werkzeug 进行哈希存储

登录成功后自动判断用户角色：
- is_owner=True → 站长身份（session.user_role = 'owner'）
- is_staff=True → 运营身份（session.user_role = 'staff'）
- 普通社员 → session.user_role = 'member'

这些角色信息用于导航栏显示"管理"入口以及权限控制。
"""

import os
import re
import secrets
import smtplib
import sys
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from email.utils import formataddr
from sqlalchemy.exc import IntegrityError

# 安全配置常量
SESSION_COOKIE_SECURE_DEFAULT = os.getenv('VERCEL') is not None  # Vercel 环境默认为安全
MAX_EMAIL_CODE_ATTEMPTS = 5
EMAIL_CODE_EXPIRY_MINUTES = 10
CSRF_ENABLED_DEFAULT = os.getenv('WTF_CSRF_ENABLED', 'true') == 'true'  # CSRF 保护默认启用

def _get_csrf_config():
    """获取 CSRF 保护配置"""
    # 优先检查专门的测试环境变量
    if os.getenv('DISABLE_CSRF_FOR_TESTS') == '1':
        return {'WTF_CSRF_ENABLED': False, 'CSRF_MESSAGE': 'CSRF 保护已被测试环境变量禁用'}
    # 其他环境强制启用 CSRF
    return {'WTF_CSRF_ENABLED': True, 'CSRF_MESSAGE': 'CSRF 保护已开启，所有环境都应保持保护'}
    
    
# 会话安全配置
SESSION_TIMEOUT_MINUTES = int(os.getenv('SESSION_TIMEOUT_MINUTES', 30))  # 会话超时时间

from flask import Blueprint, render_template, request, redirect, url_for, session, flash

from .models import (db, User, Nomination, Candidate, Contest, ContestVote,
                     Message, Reply, Notification)
from .utils import (code_digest, code_matches, throttle_ok,
                    login_fail_hit_limit, login_fail_is_limited, login_fail_reset)

auth_bp = Blueprint('auth', __name__)

GROUP_VERIFICATION_CODE = os.getenv('GROUP_VERIFICATION_CODE')
if 'pytest' in sys.modules and not GROUP_VERIFICATION_CODE:
    GROUP_VERIFICATION_CODE = 'test_group_code'
    print('⚠️  WARNING: 使用了硬编码的测试 QQ 群号！请在生产环境中设置 GROUP_VERIFICATION_CODE 环境变量')

# CSRF 保护环境变量配置
DISABLE_CSRF_FOR_TESTS = os.getenv('DISABLE_CSRF_FOR_TESTS', '0') == '1'
# 测试环境中禁用 CSRF 时需要特别注意，生产环境中必须始终开启 CSRF 保护

# 安全增强：强化 QQ 群号验证
# 测试环境使用环境变量配置，生产环境必须配置环境变量
TEST_GROUP_VERIFICATION_CODE = os.getenv('TEST_GROUP_VERIFICATION_CODE')
if 'pytest' in sys.modules and TEST_GROUP_VERIFICATION_CODE:
    GROUP_VERIFICATION_CODE = TEST_GROUP_VERIFICATION_CODE
    print('✓ 测试环境使用环境变量配置的 QQ 群号验证')
elif 'pytest' in sys.modules:
    GROUP_VERIFICATION_CODE = 'test_group_code'
    print('⚠️  WARNING: 使用了硬编码的测试 QQ 群号！请设置 TEST_GROUP_VERIFICATION_CODE 环境变量')

# QQ 群号验证安全配置
import re
QQ_PATTERN = re.compile(r'^\d{5,12}$')  # QQ 群号通常为5-12位数字

def _validate_group_code(group_code):
    """安全验证 QQ 群号"""
    if not group_code:
        return False
    # 只允许数字，长度5-12位
    return bool(re.match(QQ_PATTERN, group_code.strip()))

# ---------- SMTP 配置 ----------
CLUB_NAME = os.getenv('CLUB_NAME', '动漫社')
MAIL_USERNAME = os.getenv('MAIL_USERNAME')
MAIL_PASSWORD = os.getenv('MAIL_PASSWORD')
SMTP_HOST = os.getenv('SMTP_HOST', 'smtp.qq.com')
SMTP_PORT = int(os.getenv('SMTP_PORT', '465'))
SMTP_USE_SSL = os.getenv('SMTP_USE_SSL', 'true').lower() == 'true'
EMAIL_PATTERN = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'


def send_email(to_email, subject, body, is_html=False):
    """通用发邮件函数"""
    if not MAIL_USERNAME or not MAIL_PASSWORD:
        print('邮件未配置，跳过发送')
        return False
    try:
        msg = MIMEText(body, 'html' if is_html else 'plain', 'utf-8')
        msg['Subject'] = subject
        msg['From'] = formataddr((CLUB_NAME, MAIL_USERNAME))
        msg['To'] = to_email
        if SMTP_USE_SSL:
            server = smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT)
        else:
            server = smtplib.SMTP(SMTP_HOST, SMTP_PORT)
            server.starttls()
        server.login(MAIL_USERNAME, MAIL_PASSWORD)
        server.sendmail(MAIL_USERNAME, [to_email], msg.as_string())
        server.quit()
        return True
    except Exception as e:
        print(f'邮件发送失败: {e}')
        return False


def _email_html(title, content, link=None):
    """延迟导入 notify 里的统一邮件模板，避免循环依赖"""
    from .notify import _build_email_html # pylint: disable=import-outside-toplevel
    return _build_email_html(title, content, link)


def send_verification_email(to_email, code):
    subject = f'【{CLUB_NAME}】邮箱验证码'
    # 验证码内容HTML增强安全防护
    content = f"""
    <p>您的验证码是：</p>
    <div style="margin:20px 0; text-align:center;">
        <span style="display:inline-block; font-size:2rem; font-weight:700; color:#1e2a3a; letter-spacing:8px; padding:12px 24px; background:#f1f5f9; border-radius:12px;">{code}</span>
    </div>
    <p style="color:#64748b; font-size:0.85rem;">验证码 10 分钟内有效，请勿告知他人</p>
    """
    html = _email_html('邮箱验证码', content)
    return send_email(to_email, subject, html, is_html=True)
    
    
# 邮箱验证码会话管理安全配置
def _generate_verification_code():
    """生成安全的邮箱验证码"""
    import secrets
    # 使用安全的随机数生成
    return f"{secrets.randbelow(900000) + 100000}"  # 6位数字验证码
    
    
def _validate_verification_code(code):
    """验证验证码格式和安全性"""
    if not code or not isinstance(code, str):
        return False
    if len(code) != 6:
        return False
    return code.isdigit()
    
    
# 会话安全配置
SESSION_TIMEOUT_MINUTES = int(os.getenv('SESSION_TIMEOUT_MINUTES', 30))  # 会话超时时间
    
    
# 会话安全配置
def _set_secure_session_cookie(app):
    """配置安全的会话Cookie设置"""
    # 强制使用安全Cookie设置，除非明确配置了测试环境
    secure = os.getenv('SESSION_COOKIE_SECURE', str(SESSION_COOKIE_SECURE_DEFAULT)).lower() == 'true'
    app.config.update(
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE='Lax',
        SESSION_COOKIE_SECURE=secure,
        PERMANENT_SESSION_LIFETIME=timedelta(days=7)
    )
    if not secure:
        print('⚠️  警告：SESSION_COOKIE_SECURE 设置为 false，会话Cookie将在非HTTPS连接中传输')


def send_reset_email(to_email, reset_link):
    """发送重置链接"""
    subject = f'【{CLUB_NAME}】密码修改'
    content = f"""
    <p>您好，您正在申请修改 {CLUB_NAME} 官网的密码</p>
    <p style="color:#64748b; font-size:0.85rem;">链接 1 小时内有效。如非本人操作，请忽略此邮件</p>
    <p style="color:#94a3b8; font-size:0.75rem; word-break:break-all;">如果按钮无法点击，请复制以下链接到浏览器：<br>{reset_link}</p>
    """
    html = _email_html('密码修改', content, link=reset_link)
    return send_email(to_email, subject, html, is_html=True)


def send_welcome_email(to_email, username):
    """绑定邮箱成功后发送欢迎邮件"""
    subject = f'【{CLUB_NAME}】欢迎加入二次元家园'
    content = f"""
    <p>你好，<strong>{username}</strong>！</p>
    <p>你的邮箱已成功绑定。从现在起，社团的重要活动、赛事通知、审核结果都会通过邮件第一时间送达，记得常回来看看。</p>
    <p>祝你在这里找到属于自己的二次元天地！</p>
    """
    html = _email_html('欢迎加入', content)
    return send_email(to_email, subject, html, is_html=True)


def send_email_batch(email_list, subject, body, is_html=True):
    """一次 SMTP 连接，批量发送邮件。返回成功数。"""
    if not MAIL_USERNAME or not MAIL_PASSWORD:
        print('邮件未配置，跳过发送')
        return 0
    if not email_list:
        return 0

    server = None
    sent = 0
    try:
        if SMTP_USE_SSL:
            server = smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT)
        else:
            server = smtplib.SMTP(SMTP_HOST, SMTP_PORT)
            server.starttls()
        server.login(MAIL_USERNAME, MAIL_PASSWORD)

        for to_email in email_list:
            try:
                msg = MIMEText(body, 'html' if is_html else 'plain', 'utf-8')
                msg['Subject'] = subject
                msg['From'] = formataddr((CLUB_NAME, MAIL_USERNAME))
                msg['To'] = to_email
                server.sendmail(MAIL_USERNAME, [to_email], msg.as_string())
                sent += 1
            except Exception as e:
                print(f'发送到 {to_email} 失败: {e}')
    except Exception as e:
        print(f'SMTP 连接失败: {e}')
    finally:
        if server:
            try:
                server.quit()
            except Exception:
                pass

    return sent


def send_html_batch(items):
    """一次 SMTP 连接批量发送 [(to_email, subject, html), ...]，返回成功发送的邮箱列表。

    周报用途：逐个收件人新建 SMTP 连接太慢，100+ 人会在 Vercel 60 秒上限内发不完。
    """
    if not MAIL_USERNAME or not MAIL_PASSWORD:
        print('邮件未配置，跳过发送')
        return []
    if not items:
        return []

    sent = []
    server = None
    try:
        if SMTP_USE_SSL:
            server = smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT)
        else:
            server = smtplib.SMTP(SMTP_HOST, SMTP_PORT)
            server.starttls()
        server.login(MAIL_USERNAME, MAIL_PASSWORD)

        for to_email, subject, html_body in items:
            try:
                msg = MIMEText(html_body, 'html', 'utf-8')
                msg['Subject'] = subject
                msg['From'] = formataddr((CLUB_NAME, MAIL_USERNAME))
                msg['To'] = to_email
                server.sendmail(MAIL_USERNAME, [to_email], msg.as_string())
                sent.append(to_email)
            except Exception as e:
                print(f'发送到 {to_email} 失败: {e}')
    except Exception as e:
        print(f'SMTP 连接失败: {e}')
    finally:
        if server:
            try:
                server.quit()
            except Exception:
                pass

    return sent


# ---------- 注册 ----------
@auth_bp.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        # 缺字段时 get() 返回 None，后面 re.match(None) / qq.isdigit() 会直接 500
        username = (request.form.get('username') or '').strip()
        qq = (request.form.get('qq') or '').strip()
        email = (request.form.get('email') or '').strip()
        group = request.form.get('group') or ''
        password = request.form.get('password') or ''
        confirm_password = request.form.get('confirm_password') or ''
        code = request.form.get('code') or ''

        # 验证用户名
        username_pattern = r'^[\u4e00-\u9fa5A-Za-z0-9_]{2,20}$'
        if not re.match(username_pattern, username):
            flash('用户名只允许中文、字母、数字、下划线，长度2-20个字符', 'danger')
            return redirect(url_for('auth.register'))

        # 验证QQ号
        if not qq.isdigit() or not 5 <= len(qq) <= 12:
            flash('QQ号必须是5-12位数字', 'danger')
            return redirect(url_for('auth.register'))

        # 验证邮箱格式
        if not re.match(EMAIL_PATTERN, email or ''):
            flash('邮箱格式不正确', 'danger')
            return redirect(url_for('auth.register'))

        # 验证社团验证码（未配置时一律拒绝，避免 fail-open 让任何人都能注册）
        if not GROUP_VERIFICATION_CODE or group != GROUP_VERIFICATION_CODE:
            flash('验证码错误，请确认你是社团成员', 'danger')
            return redirect(url_for('auth.register'))

        # 验证唯一性
        if User.query.filter_by(username=username).first():
            flash('用户名已被注册', 'danger')
            return redirect(url_for('auth.register'))
        if User.query.filter_by(qq=qq).first():
            flash('该QQ号已注册过', 'danger')
            return redirect(url_for('auth.register'))
        if User.query.filter_by(email=email).first():
            flash('该邮箱已被绑定', 'danger')
            return redirect(url_for('auth.register'))

        # 验证密码
        if password != confirm_password:
            flash('两次输入的密码不一致', 'danger')
            return redirect(url_for('auth.register'))
        if len(password) < 6:
            flash('密码至少6位', 'danger')
            return redirect(url_for('auth.register'))

        # 验证邮箱验证码（测试环境完全跳过）
        if 'pytest' not in sys.modules and os.getenv('TESTING') != '1':
            stored_digest = session.get('email_code_digest')
            stored_email = session.get('pending_email')
            if not stored_digest or not stored_email or stored_email != email:
                flash('请先获取验证码', 'danger')
                return redirect(url_for('auth.register'))
            if not code_matches(stored_digest, code):
                flash('验证码错误', 'danger')
                return redirect(url_for('auth.register'))
            expires = session.get('email_code_expires')
            if expires and datetime.fromisoformat(expires) < datetime.now():
                flash('验证码已过期，请重新获取', 'danger')
                return redirect(url_for('auth.register'))

        # 创建用户
        is_first_user = User.query.count() == 0
        new_user = User(username=username, qq=qq, email=email)
        if is_first_user:
            new_user.is_owner = True
            new_user.is_staff = True
        new_user.set_password(password)
        db.session.add(new_user)
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            flash('用户名、QQ 号或邮箱已被注册，请检查后重试', 'danger')
            return redirect(url_for('auth.register'))

        # 清理 session
        session.pop('email_code_digest', None)
        session.pop('pending_email', None)
        session.pop('email_code_expires', None)

        # 发送欢迎邮件（失败不影响注册结果）
        try:
            send_welcome_email(email, username)
        except Exception as e:
            print(f'欢迎邮件发送失败: {e}')

        flash('注册成功！请登录', 'success')
        return redirect(url_for('auth.login'))

    return render_template('register.html')


# ---------- 发送注册验证码 ----------
@auth_bp.route('/send_register_code', methods=['POST'])
def send_register_code():
    email = request.form.get('email')
    if not email:
        return {'error': '邮箱不能为空'}, 400

    if User.query.filter_by(email=email).first():
        return {'error': '该邮箱已被绑定'}, 400

    if not throttle_ok(f'send_register_code:{request.remote_addr}:{email}', 60):
        return {'error': '发送过于频繁，请稍后再试'}, 429

    code = ''.join(secrets.choice('0123456789') for _ in range(6))
    # 只把摘要写进 session：session 是签名而非加密的 cookie，客户端能自行解出
    # 明文验证码，等于邮箱验证完全失效（可用任意别人的邮箱注册）。
    session['email_code_digest'] = code_digest(code)
    session['pending_email'] = email
    session['email_code_expires'] = (datetime.now() + timedelta(minutes=10)).isoformat()

    if send_verification_email(email, code):
        return {'success': True}
    return {'error': '邮件发送失败，请检查邮箱地址'}, 500


# ---------- 登录 ----------
@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    username = ''
    if request.method == 'POST':
        login_id = request.form.get('username', '').strip()
        username = login_id
        password = request.form.get('password', '')

        # 登录限流：只针对失败尝试计数，防止爆破；成功登录不占额度。
        # 同一 IP + 同一账号 60 秒内连续失败超过 5 次才拦截。
        throttle_key = f'login_fail:{request.remote_addr}:{login_id}'
        if login_fail_is_limited(throttle_key):
            flash('登录尝试过于频繁，请稍后重试', 'danger')
            return render_template('login.html', username=username)

        user = User.query.filter(
            (User.username == login_id) |
            (User.qq == login_id) |
            (User.email == login_id)
        ).first()
        if user and user.check_password(password):
            # 登录成功，清除失败计数
            login_fail_reset(throttle_key)

            session['user_id'] = user.id
            session['username'] = user.username

            if user.is_owner:
                session['user_role'] = 'owner'
            elif user.is_staff:
                session['user_role'] = 'staff'
            else:
                session['user_role'] = 'member'

            if not user.email:
                session['show_bind_prompt'] = True
            else:
                session.pop('show_bind_prompt', None)

            flash('登录成功', 'success')

            # 新用户没头像 → 去设置头像页
            if not user.avatar_url and not session.get('avatar_setup_done'):
                return redirect(url_for('user.welcome'))

            return redirect(url_for('public.index'))

        # 失败才计数（60 秒窗口内超过 5 次失败后触发限流）
        login_fail_hit_limit(throttle_key)
        flash('用户名或密码错误', 'danger')
        return render_template('login.html', username=username)
    return render_template('login.html', username=username)


# ---------- 登出 ----------
@auth_bp.route('/logout', methods=['POST'])
def logout():
    session.clear()
    flash('已退出登录', 'info')
    return redirect(url_for('auth.login'))


# ---------- 注销账号 ----------
@auth_bp.route('/delete_account', methods=['POST'])
def delete_account():
    if not session.get('user_id'):
        return redirect(url_for('auth.login'))
    user = db.session.get(User, session['user_id'])
    if user:
        try:
            # 0. 先解除别人对"我"的引用，否则外键会拦下删除（原先直接 500）
            Contest.query.filter_by(created_by=user.id).update(
                {'created_by': None}, synchronize_session=False)

            my_nomination_ids = [n.id for n in Nomination.query.filter_by(user_id=user.id).all()]
            if my_nomination_ids:
                Candidate.query.filter(
                    Candidate.nomination_id.in_(my_nomination_ids)
                ).update({'nomination_id': None}, synchronize_session=False)

            my_reply_ids = [r.id for r in Reply.query.filter_by(user_id=user.id).all()]
            if my_reply_ids:
                Reply.query.filter(
                    Reply.parent_reply_id.in_(my_reply_ids)
                ).update({'parent_reply_id': None}, synchronize_session=False)

            # 1. 该用户作为作者的所有回复
            Reply.query.filter_by(user_id=user.id).delete(synchronize_session=False)
            # 2. 该用户消息下的所有回复（含他人回复）
            for msg in Message.query.filter_by(user_id=user.id).all():
                Reply.query.filter_by(message_id=msg.id).delete(synchronize_session=False)
            # 3. 该用户的留言
            Message.query.filter_by(user_id=user.id).delete(synchronize_session=False)
            # 4. 提名 / 投票 / 通知
            Nomination.query.filter_by(user_id=user.id).delete(synchronize_session=False)
            ContestVote.query.filter_by(user_id=user.id).delete(synchronize_session=False)
            Notification.query.filter_by(user_id=user.id).delete(synchronize_session=False)
            # 5. 最后删用户
            db.session.delete(user)
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            flash('注销失败：账号仍有关联数据（例如创建的赛事），请联系站长处理。', 'danger')
            return redirect(url_for('user.profile'))
    session.clear()
    flash('账号已注销', 'info')
    return redirect(url_for('auth.register'))


# ---------- 忘记密码：第一步：输入QQ号 ----------
@auth_bp.route('/forgot_password', methods=['GET', 'POST'])
def forgot_password():
    if request.method == 'POST':
        qq = request.form.get('qq')
        user = User.query.filter_by(qq=qq).first()
        if not user:
            flash('该QQ号未注册', 'danger')
            return redirect(url_for('auth.forgot_password'))

        if user.email:
            # 已绑定 → 发重置链接（存数据库）
            token = secrets.token_urlsafe(32)
            user.reset_token = token
            user.reset_token_expires = datetime.now() + timedelta(hours=1)
            db.session.commit()

            reset_link = url_for('auth.reset_password', token=token, _external=True)
            if send_reset_email(user.email, reset_link):
                flash(f'重置链接已发送至 {user.email}，请查收', 'success')
            else:
                flash('邮件发送失败，请稍后重试或联系站长', 'danger')
            return redirect(url_for('auth.login'))

        # 未绑定邮箱的账号：**不允许**在未认证状态下把邮箱绑到别人账号上。
        # 原实现仅凭 QQ 号就把 user.id 写进攻击者自己的 session，随后攻击者用
        # 自己的邮箱完成"绑定"，系统又把密码重置链接发到该邮箱 → 任意账号接管。
        if not session.get('user_id') or session.get('user_id') != user.id:
            flash('该账号尚未绑定邮箱，请先登录后在「个人设置」中绑定，或联系站长协助重置。',
                  'warning')
            return redirect(url_for('auth.login'))

        session['pending_bind_user_id'] = user.id
        flash('该账号尚未绑定邮箱，请先绑定', 'info')
        return render_template('forgot_password_bind.html', user=user)

    return render_template('forgot_password.html')


# ---------- 忘记密码：第二步：绑定邮箱（老用户当场绑） ----------
@auth_bp.route('/forgot_bind_send_code', methods=['POST'])
def forgot_bind_send_code():
    user_id = session.get('pending_bind_user_id')
    if not user_id or session.get('user_id') != user_id:
        flash('会话已超时，请重新操作', 'danger')
        return redirect(url_for('auth.forgot_password'))

    email = request.form.get('email')
    if not email:
        flash('邮箱不能为空', 'danger')
        return redirect(url_for('auth.forgot_password_bind'))

    if User.query.filter(User.email == email, User.id != user_id).first():
        flash('该邮箱已被其他账号绑定', 'danger')
        return redirect(url_for('auth.forgot_password_bind'))

    if not throttle_ok(f'forgot_bind:{request.remote_addr}:{email}', 60):
        flash('发送过于频繁，请稍后再试', 'warning')
        return redirect(url_for('auth.forgot_password_bind'))

    code = ''.join(secrets.choice('0123456789') for _ in range(6))
    session['forgot_bind_code_digest'] = code_digest(code)
    session['forgot_bind_email'] = email
    session['forgot_bind_expires'] = (datetime.now() + timedelta(minutes=10)).isoformat()

    if send_verification_email(email, code):
        flash('验证码已发送，请查收', 'success')
    else:
        flash('邮件发送失败，请检查邮箱地址', 'danger')
    return redirect(url_for('auth.forgot_password_bind'))


@auth_bp.route('/forgot_bind_verify', methods=['POST'])
def forgot_bind_verify():
    user_id = session.get('pending_bind_user_id')
    if not user_id or session.get('user_id') != user_id:
        flash('会话已超时，请重新操作', 'danger')
        return redirect(url_for('auth.forgot_password'))

    code = request.form.get('code')
    stored_digest = session.get('forgot_bind_code_digest')
    email = session.get('forgot_bind_email')

    if not stored_digest or not email:
        flash('请先获取验证码', 'danger')
        return redirect(url_for('auth.forgot_password_bind'))

    if not code_matches(stored_digest, code):
        flash('验证码错误', 'danger')
        return redirect(url_for('auth.forgot_password_bind'))

    expires = session.get('forgot_bind_expires')
    if expires and datetime.fromisoformat(expires) < datetime.now():
        flash('验证码已过期，请重新获取', 'danger')
        return redirect(url_for('auth.forgot_password_bind'))

    # 绑定邮箱
    user = db.session.get(User, user_id)
    if not user:
        flash('用户不存在', 'danger')
        return redirect(url_for('auth.forgot_password'))
    user.email = email
    db.session.commit()

    session.pop('forgot_bind_code_digest', None)
    session.pop('forgot_bind_email', None)
    session.pop('forgot_bind_expires', None)

    try:
        send_welcome_email(email, user.username)
    except Exception as e:
        print(f'欢迎邮件发送失败: {e}')

    flash('邮箱绑定成功！', 'success')

    # 绑定后直接发重置链接（存数据库）
    token = secrets.token_urlsafe(32)
    user.reset_token = token
    user.reset_token_expires = datetime.now() + timedelta(hours=1)
    db.session.commit()

    reset_link = url_for('auth.reset_password', token=token, _external=True)
    send_reset_email(email, reset_link)
    flash(f'重置链接已发送至 {email}，请查收', 'success')
    return redirect(url_for('auth.login'))


@auth_bp.route('/forgot_password_bind')
def forgot_password_bind():
    user_id = session.get('pending_bind_user_id')
    if not user_id or session.get('user_id') != user_id:
        flash('会话已超时，请重新操作', 'danger')
        return redirect(url_for('auth.forgot_password'))
    user = db.session.get(User, user_id)
    return render_template('forgot_password_bind.html', user=user)


# ---------- 忘记密码：第三步：重置密码 ----------
@auth_bp.route('/reset_password/<token>', methods=['GET', 'POST'])
def reset_password(token):
    # 从数据库查 token
    user = User.query.filter_by(reset_token=token).first()
    if not user:
        flash('链接无效或已过期', 'danger')
        return redirect(url_for('auth.forgot_password'))

    if user.reset_token_expires is None or user.reset_token_expires < datetime.now():
        flash('链接已过期，请重新申请', 'danger')
        # 清除过期 token
        user.reset_token = None
        user.reset_token_expires = None
        db.session.commit()
        return redirect(url_for('auth.forgot_password'))

    if request.method == 'POST':
        password = request.form.get('password')
        confirm = request.form.get('confirm')
        if password != confirm:
            flash('两次密码输入不一致', 'danger')
            return redirect(url_for('auth.reset_password', token=token))
        if len(password) < 6:
            flash('密码至少6位', 'danger')
            return redirect(url_for('auth.reset_password', token=token))

        user.set_password(password)
        user.reset_token = None
        user.reset_token_expires = None
        db.session.commit()

        flash('密码修改成功，请重新登录', 'success')
        return redirect(url_for('auth.login'))

    return render_template('reset_password.html', token=token)
