"""
动漫社官网 · 用户中心模块
====================================

本模块处理与个人账号相关的功能：

1. 个人设置 (/profile) —— 支持：
   - 更换头像（支持 jpg/png/gif，限制 2MB）
   - 修改用户名（需唯一性校验）
   - 修改密码（需验证原密码）

2. 个人主页 (/user?name=xxx) —— 公开展示用户信息：
   - 用户头像（用边框颜色区分身份：站长金色，运营深色）
   - 最近留言（最多10条）
   - 已通过审核的番剧推荐

3. 头像获取 (/avatar/<user_id>) —— 返回用户头像图片：
   - 从数据库读取二进制数据
   - 若未设置则返回默认透明图
   - 设置浏览器缓存（1天）
"""

import base64
import re
import secrets
from datetime import datetime, timedelta

from flask import Blueprint, render_template, request, redirect, url_for, session, flash, abort, Response, jsonify
from sqlalchemy import func

from .config import AVATAR_MAX_SIZE
from .models import db, User, Message, Notification
from .preferences import (get_preferences, muted_types,
                          normalize_preferences, save_preferences)
from .utils import (allowed_file, compress_image, get_or_404,
                    upload_avatar_to_storage, code_digest, code_matches)
from .auth import send_verification_email, send_welcome_email

user_bp = Blueprint('user', __name__)

@user_bp.route('/welcome', methods=['GET', 'POST'])
def welcome():
    """新用户引导：设置头像"""
    if not session.get('user_id'):
        return redirect(url_for('auth.login'))

    user = db.session.get(User, session['user_id'])
    if not user:
        session.clear()
        return redirect(url_for('auth.login'))

    if request.method == 'POST':
        action = request.form.get('action')

        # 跳过
        if action == 'skip':
            session['avatar_setup_done'] = True
            return redirect(url_for('public.index'))

        # 上传头像
        if 'avatar' in request.files:
            file = request.files['avatar']
            if file and allowed_file(file.filename):
                if request.content_length and request.content_length > AVATAR_MAX_SIZE:
                    flash('头像文件不能超过2MB', 'danger')
                    return redirect(url_for('user.welcome'))
                try:
                    raw_data = file.read()
                    compressed = compress_image(raw_data, max_size=(150, 150), quality=75)
                except Exception:
                    flash('图片格式无效或已损坏', 'danger')
                    return redirect(url_for('user.welcome'))

                try:
                    url = upload_avatar_to_storage(user.id, compressed)
                    user.avatar_url = url
                    db.session.commit()
                except Exception as e:
                    flash(f'头像上传失败: {e}', 'danger')
                    return redirect(url_for('user.welcome'))

                session['avatar_setup_done'] = True
                flash('头像设置成功！', 'success')
                return redirect(url_for('public.index'))
            flash('不支持的文件类型（支持 png, jpg, jpeg, gif）', 'danger')
            return redirect(url_for('user.welcome'))

        return redirect(url_for('user.welcome'))

    return render_template('welcome.html', user=user)

@user_bp.route('/profile', methods=['GET', 'POST'])
def profile():
    if not session.get('user_id'):
        flash('请先登录', 'warning')
        return redirect(url_for('auth.login'))

    user = db.session.get(User, session['user_id'])
    if not user:
        session.clear()
        return redirect(url_for('auth.login'))

    if request.method == 'POST':
        action = request.form.get('action')

        # ---------- 更换头像 ----------
        if action == 'change_avatar':
            if 'avatar' in request.files:
                file = request.files['avatar']
                if file and allowed_file(file.filename):
                    if request.content_length and request.content_length > AVATAR_MAX_SIZE:
                        flash('头像文件不能超过2MB', 'danger')
                    else:
                        try:
                            raw_data = file.read()
                            compressed = compress_image(raw_data, max_size=(200, 200), quality=80)
                        except Exception:
                            flash('图片格式无效或已损坏', 'danger')
                            return redirect(url_for('user.profile'))
                        try:
                            url = upload_avatar_to_storage(user.id, compressed)
                            user.avatar_url = url
                            db.session.commit()
                            flash('头像更新成功', 'success')
                        except Exception as e:
                            flash(f'头像上传失败: {e}', 'danger')
                else:
                    flash('不支持的文件类型（支持 png, jpg, jpeg, gif）', 'danger')
            return redirect(url_for('user.profile'))

        # ---------- 修改用户名 ----------
        if action == 'change_username':
            new_username = request.form.get('new_username', '').strip()
            # 与注册使用同一套校验（原先只校验长度，`');alert(1)//` 这类字符
            # 能进库，并被拼进后台模板的 onsubmit JS 字符串 → 打向站长的存储型 XSS）
            username_pattern = r'^[\u4e00-\u9fa5A-Za-z0-9_]{2,20}$'
            if not new_username:
                flash('用户名不能为空', 'danger')
            elif not re.match(username_pattern, new_username):
                flash('用户名只允许中文、字母、数字、下划线，长度2-20个字符', 'danger')
            else:
                existing = User.query.filter(User.username == new_username, User.id != user.id).first()
                if existing:
                    flash('该用户名已被占用', 'danger')
                else:
                    old_username = user.username
                    user.username = new_username
                    db.session.commit()
                    session['username'] = new_username
                    flash(f'用户名已从 "{old_username}" 更新为 "{new_username}"', 'success')
            return redirect(url_for('user.profile'))

        # ---------- 绑定邮箱：验证验证码 ----------
        # 绑定邮箱：验证验证码
        if action == 'verify_email_code':
            code = request.form.get('code')
            stored_digest = session.get('profile_email_digest')
            pending_email = session.get('profile_pending_email')

            if not pending_email:
                return jsonify({'error': '请先发送验证码'}), 400
            if not code_matches(stored_digest, code):
                return jsonify({'error': '验证码错误'}), 400

            expires = session.get('profile_email_expires')
            if expires and datetime.fromisoformat(expires) < datetime.now():
                return jsonify({'error': '验证码已过期，请重新获取'}), 400

            user.email = pending_email
            db.session.commit()
            session.pop('profile_email_digest', None)
            session.pop('profile_pending_email', None)
            session.pop('profile_email_expires', None)
            session.pop('show_bind_prompt', None)

            # 发送欢迎邮件（失败不影响绑定结果）
            try:
                send_welcome_email(pending_email, user.username)
            except Exception as e:
                print(f'欢迎邮件发送失败: {e}')

            return jsonify({'success': True, 'message': '邮箱绑定成功', 'email': pending_email})

    return render_template('profile.html', user=user)


@user_bp.route('/change_password', methods=['GET', 'POST'])
def change_password():
    """独立的修改密码页面"""
    if not session.get('user_id'):
        flash('请先登录', 'warning')
        return redirect(url_for('auth.login'))

    user = db.session.get(User, session['user_id'])
    if not user:
        session.clear()
        return redirect(url_for('auth.login'))

    if request.method == 'POST':
        old = request.form.get('old_password')
        new = request.form.get('new_password')
        confirm = request.form.get('confirm_password')

        if not user.check_password(old):
            flash('原密码错误', 'danger')
            return redirect(url_for('user.change_password'))
        if new != confirm:
            flash('两次输入的新密码不一致', 'danger')
            return redirect(url_for('user.change_password'))
        if len(new) < 6:
            flash('新密码至少6位', 'danger')
            return redirect(url_for('user.change_password'))
        if new == old:
            flash('新密码不能与原密码相同', 'danger')
            return redirect(url_for('user.change_password'))
        user.set_password(new)
        db.session.commit()
        flash('密码修改成功', 'success')
        return redirect(url_for('user.profile'))

    return render_template('change_password.html', user=user)


@user_bp.route('/bind_email', methods=['GET', 'POST'])
def bind_email():
    """独立的绑定/更换邮箱页面"""
    if not session.get('user_id'):
        flash('请先登录', 'warning')
        return redirect(url_for('auth.login'))

    user = db.session.get(User, session['user_id'])
    if not user:
        session.clear()
        return redirect(url_for('auth.login'))

    if request.method == 'POST':
        action = request.form.get('action')

        # ---------- 发送验证码 ----------
        if action == 'send_email_code':
            email = request.form.get('email')
            if not email:
                return jsonify({'error': '邮箱不能为空'}), 400
            if User.query.filter(User.email == email, User.id != user.id).first():
                return jsonify({'error': '该邮箱已被其他用户绑定'}), 400

            code = ''.join(secrets.choice('0123456789') for _ in range(6))
            session['profile_email_digest'] = code_digest(code)
            session['profile_pending_email'] = email
            session['profile_email_expires'] = (datetime.now() + timedelta(minutes=10)).isoformat()

            if send_verification_email(email, code):
                return jsonify({'success': True, 'message': '验证码已发送'})
            return jsonify({'error': '邮件发送失败，请检查邮箱地址'}), 500

        # ---------- 验证并绑定 ----------
        if action == 'verify_email_code':
            code = request.form.get('code')
            stored_digest = session.get('profile_email_digest')
            pending_email = session.get('profile_pending_email')

            if not pending_email:
                return jsonify({'error': '请先发送验证码'}), 400
            if not code_matches(stored_digest, code):
                return jsonify({'error': '验证码错误'}), 400

            expires = session.get('profile_email_expires')
            if expires and datetime.fromisoformat(expires) < datetime.now():
                return jsonify({'error': '验证码已过期，请重新获取'}), 400

            user.email = pending_email
            db.session.commit()
            session.pop('profile_email_digest', None)
            session.pop('profile_pending_email', None)
            session.pop('profile_email_expires', None)
            session.pop('show_bind_prompt', None)

            try:
                send_welcome_email(pending_email, user.username)
            except Exception as e:
                print(f'欢迎邮件发送失败: {e}')

            return jsonify({'success': True, 'message': '邮箱绑定成功', 'email': pending_email})

    return render_template('bind_email.html', user=user)


# ---------- 个人主页 ----------
@user_bp.route('/user')
def user_profile():
    username = request.args.get('name')
    if not username:
        abort(404)
    user = User.query.filter(func.lower(User.username) == func.lower(username.strip())).first()
    if not user:
        abort(404)
    messages = Message.query.filter_by(user_id=user.id).order_by(Message.timestamp.desc()).limit(10).all()
    for msg in messages:
        msg.timestamp = msg.timestamp + timedelta(hours=8)
    return render_template('user_profile.html', user=user, messages=messages)


# ---------- 头像 ----------
@user_bp.route('/avatar/<int:user_id>')
def get_avatar(user_id):
    """头像兜底路由"""
    user = get_or_404(User, user_id)

    if user.avatar_url:
        return redirect(user.avatar_url)

    default = base64.b64decode(
        'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==')
    response = Response(default, mimetype='image/png')
    response.headers['Cache-Control'] = 'public, max-age=86400, s-maxage=86400'
    return response


@user_bp.route('/dismiss_bind_prompt')
def dismiss_bind_prompt():
    session.pop('show_bind_prompt', None)
    return redirect(url_for('public.index'))


@user_bp.route('/notifications')
def notifications():
    """通知列表页（已静音的类型不出现，但仍保留在库中，取消静音后可见）"""
    if not session.get('user_id'):
        return redirect(url_for('auth.login'))

    muted = muted_types(db.session.get(User, session['user_id']))
    query = Notification.query.filter_by(user_id=session['user_id'])
    if muted:
        query = query.filter(~Notification.type.in_(muted))
    items = query.order_by(Notification.created_at.desc()).all()
    # 时间转东八区
    for n in items:
        n.created_at = n.created_at + timedelta(hours=8)

    return render_template('notifications.html', notifications=items, muted_types=muted)


@user_bp.route('/notifications/read/<int:nid>')
def notification_read(nid):
    """标记单条已读并跳转"""
    if not session.get('user_id'):
        return redirect(url_for('auth.login'))
    n = db.session.get(Notification, nid)
    if n and n.user_id == session['user_id']:
        n.is_read = True
        db.session.commit()
        # 通知 link 目前都由 url_for() 生成，但它是可写数据库字段：将来只要
        # 有任何路径把用户输入写进 link，这里就是开放重定向。只放行站内相对
        # 路径（以 / 开头且不是 //host 形式），其余一律回落到通知列表页。
        if n.link and n.link.startswith('/') and not n.link.startswith('//'):
            return redirect(n.link)
    return redirect(url_for('user.notifications'))


@user_bp.route('/notifications/read_all')
def notification_read_all():
    """全部标记已读（已静音的类型不参与，避免它们被静默置读）"""
    if not session.get('user_id'):
        return redirect(url_for('auth.login'))
    muted = muted_types(db.session.get(User, session['user_id']))
    query = Notification.query.filter_by(
        user_id=session['user_id'],
        is_read=False
    )
    if muted:
        query = query.filter(~Notification.type.in_(muted))
    query.update({'is_read': True})
    db.session.commit()
    flash('已全部标记为已读', 'success')
    return redirect(url_for('user.notifications'))


@user_bp.route('/api/notifications/count')
def notification_count():
    """返回未读数（给导航栏铃铛用；已静音的类型不计入）"""
    if not session.get('user_id'):
        return jsonify({'count': 0})
    muted = muted_types(db.session.get(User, session['user_id']))
    query = Notification.query.filter_by(
        user_id=session['user_id'],
        is_read=False
    )
    if muted:
        query = query.filter(~Notification.type.in_(muted))
    return jsonify({'count': query.count()})


@user_bp.route('/settings/notifications', methods=['POST'])
def save_notification_settings():
    """保存「通知与免打扰」设置（账号级，存 users.preferences）"""
    if not session.get('user_id'):
        return redirect(url_for('auth.login'))
    user = db.session.get(User, session['user_id'])
    if user is None:
        return redirect(url_for('auth.login'))

    prefs = get_preferences(user)
    notify = dict(prefs['notify'])
    notify['muted_types'] = request.form.getlist('muted_types')
    notify['email_enabled'] = request.form.get('email_enabled') is not None
    notify['badge_enabled'] = request.form.get('badge_enabled') is not None
    notify['badge_poll_seconds'] = request.form.get('badge_poll_seconds')

    save_preferences(user, normalize_preferences({'notify': notify}))
    db.session.commit()
    flash('通知设置已保存', 'success')
    return redirect(url_for('public.settings') + '#notify-section')
