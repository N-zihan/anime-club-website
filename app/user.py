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
   - 有自定义头像：重定向到存储地址
   - 没有自定义头像：按用户 id 生成「渐变底色 + 用户名首字」的 SVG 头像（原先返回 1×1 透明图，
     被拉伸后就是一块空白方块，视觉上很丑）
   - 设置浏览器缓存（1天）
"""

import re
import secrets
from datetime import datetime, timedelta, timezone
from html import escape as html_escape

from flask import Blueprint, render_template, request, redirect, url_for, session, flash, abort, Response, jsonify
from sqlalchemy import func

from .config import AVATAR_MAX_SIZE
from .models import (db, User, Message, Notification, AIPersona, AIChatMessage,
                     AnimeListEntry)
from . import ai_friend as ai_friend_mod
from .preferences import (NOTIFY_LABELS, get_preferences, muted_types,
                          normalize_preferences, save_preferences)
from .utils import (allowed_file, compress_image, get_or_404, cn_now,
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
AVATAR_PALETTE = (
    ('#5b9bea', '#3f7fd0'),   # 蓝
    ('#7c6cf0', '#5b4fd0'),   # 紫
    ('#e46c9a', '#c74f80'),   # 粉
    ('#3fb8a8', '#2b9488'),   # 青
    ('#e0a13c', '#c4831f'),   # 橙
    ('#6c8ae4', '#4a68c9'),   # 靛
)


def _fallback_avatar_svg(user) -> str:
    """按用户生成「渐变底色 + 用户名首字」的 SVG 头像。

    没有自定义头像的社员占大多数，返回 1×1 透明图会让头像位看起来是空白方块，
    这里直接给一枚可缓存的 SVG：颜色按 id 轮转，首字取用户名第一个字符。
    """
    user_id = getattr(user, 'id', 0) or 0
    start, end = AVATAR_PALETTE[user_id % len(AVATAR_PALETTE)]
    name = (getattr(user, 'username', '') or '').strip()
    initial = html_escape(name[:1].upper() or '?')
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 80 80" role="img">'
        f'<defs><linearGradient id="g{user_id}" x1="0" y1="0" x2="1" y2="1">'
        f'<stop offset="0" stop-color="{start}"/><stop offset="1" stop-color="{end}"/>'
        '</linearGradient></defs>'
        f'<rect width="80" height="80" rx="40" fill="url(#g{user_id})"/>'
        '<text x="40" y="53" text-anchor="middle" font-size="34" font-weight="600"'
        f' font-family="system-ui, -apple-system, Segoe UI, sans-serif" fill="#ffffff">{initial}</text>'
        '</svg>'
    )


@user_bp.route('/avatar/<int:user_id>')
def get_avatar(user_id):
    """头像兜底路由：有自定义头像就跳转，否则返回按用户生成的 SVG 头像"""
    user = get_or_404(User, user_id)

    if user.avatar_url:
        return redirect(user.avatar_url)

    response = Response(_fallback_avatar_svg(user), mimetype='image/svg+xml')
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

    page = max(1, request.args.get('page', 1, type=int) or 1)
    per_page = 30
    total = query.count()
    total_pages = max(1, (total + per_page - 1) // per_page)
    page = min(page, total_pages)
    items = (query.order_by(Notification.created_at.desc(), Notification.id.desc())
             .offset((page - 1) * per_page)
             .limit(per_page)
             .all())
    # 时间转东八区
    for n in items:
        n.created_at = n.created_at + timedelta(hours=8)

    return render_template('notifications.html', notifications=items, muted_types=muted,
                           notify_labels=NOTIFY_LABELS, page=page,
                           total_pages=total_pages, total=total)


@user_bp.route('/notifications/clear_read', methods=['POST'])
def notifications_clear_read():
    """清空已读通知（分页多了以后给用户一个收拾抽屉的按钮）"""
    if not session.get('user_id'):
        return redirect(url_for('auth.login'))
    deleted = Notification.query.filter_by(user_id=session['user_id'],
                                           is_read=True).delete(synchronize_session=False)
    db.session.commit()
    flash(f'已清理 {deleted} 条已读通知', 'success')
    return redirect(url_for('user.notifications'))


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


# ========== AI 虚拟好友 ==========
PERSONA_MAX_PER_USER = 5
PERSONA_MAX_PER_DAY = 3
CHAT_PAGE_SIZE = 20


def _cn_today_start():
    """东八区「今天 00:00」对应的 UTC naive 时间（库里存 naive UTC）"""
    now = cn_now()
    start_cn = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return start_cn - timedelta(hours=8)


def _validate_persona(form):
    """校验人设表单，返回 (data, errors)"""
    data = {
        'name': (form.get('name') or '').strip()[:30],
        'source': (form.get('source') or '').strip()[:50],
        'personality': (form.get('personality') or '').strip()[:200],
        'speech_style': (form.get('speech_style') or '').strip()[:200],
        'catchphrases': (form.get('catchphrases') or '').strip()[:200],
        'address_user': (form.get('address_user') or '').strip()[:30],
    }
    errors = []
    if not data['name']:
        errors.append('角色名不能为空')
    if not data['personality']:
        errors.append('请至少填一个性格标签')
    if len(data['speech_style']) < 2:
        errors.append('说话风格至少 2 个字')

    combined = ' '.join(v for v in data.values() if v)
    if ai_friend_mod.check_input(combined):
        errors.append('内容包含不允许的表述，请修改后重试')
    return data, errors


def _persona_to_dict(persona):
    return {
        'id': persona.id,
        'name': persona.name,
        'source': persona.source or '',
        'personality': persona.personality,
        'speech_style': persona.speech_style,
        'catchphrases': persona.catchphrases or '',
        'address_user': persona.address_user or '',
        'is_public': bool(persona.is_public),
        'is_mine': True,
    }


def _select_persona(persona_id, user_id):
    """取聊天使用的 persona：自己的或公开的，其余一律视为无（用默认看板娘）"""
    if not persona_id:
        return None
    try:
        pid = int(persona_id)
    except (TypeError, ValueError):
        return None
    persona = db.session.get(AIPersona, pid)
    if persona is None or persona.status != 'active':
        return None
    if persona.user_id != user_id and not persona.is_public:
        return None
    return persona


@user_bp.route('/ai_friend')
def ai_friend():
    """AI 虚拟好友：聊天 + 我的角色 / 公开角色"""
    if not session.get('user_id'):
        return redirect(url_for('auth.login'))
    user_id = session['user_id']

    mine = AIPersona.query.filter_by(user_id=user_id, status='active') \
        .order_by(AIPersona.created_at.desc()).all()
    public = AIPersona.query.filter(AIPersona.is_public.is_(True),
                                    AIPersona.status == 'active',
                                    AIPersona.user_id != user_id) \
        .order_by(AIPersona.created_at.desc()).limit(20).all()

    history = AIChatMessage.query.filter_by(user_id=user_id) \
        .order_by(AIChatMessage.created_at.desc()).limit(CHAT_PAGE_SIZE).all()
    history.reverse()

    used = AIChatMessage.query.filter(
        AIChatMessage.user_id == user_id,
        AIChatMessage.role == 'user',
        AIChatMessage.created_at >= _cn_today_start(),
    ).count()

    return render_template(
        'ai_friend.html',
        my_personas=mine,
        public_personas=public,
        history=history,
        ai_enabled=ai_friend_mod.is_enabled(),
        models=ai_friend_mod.models(),
        daily_limit=ai_friend_mod.daily_limit(),
        used_today=used,
        max_personas=PERSONA_MAX_PER_USER,
    )


@user_bp.route('/ai_friend/personas', methods=['POST'])
def ai_friend_persona_create():
    if not session.get('user_id'):
        return redirect(url_for('auth.login'))
    user_id = session['user_id']

    if AIPersona.query.filter_by(user_id=user_id, status='active').count() >= PERSONA_MAX_PER_USER:
        flash(f'最多创建 {PERSONA_MAX_PER_USER} 个角色', 'danger')
        return redirect(url_for('user.ai_friend') + '#personas')

    created_today = AIPersona.query.filter(
        AIPersona.user_id == user_id,
        AIPersona.created_at >= _cn_today_start(),
    ).count()
    if created_today >= PERSONA_MAX_PER_DAY:
        flash(f'每天最多新建 {PERSONA_MAX_PER_DAY} 个角色，明天再来吧', 'danger')
        return redirect(url_for('user.ai_friend') + '#personas')

    data, errors = _validate_persona(request.form)
    if errors:
        flash('；'.join(errors), 'danger')
        return redirect(url_for('user.ai_friend') + '#personas')

    persona = AIPersona(
        user_id=user_id,
        name=data['name'],
        source=data['source'] or None,
        personality=data['personality'],
        speech_style=data['speech_style'],
        catchphrases=data['catchphrases'] or None,
        address_user=data['address_user'] or None,
        is_public=request.form.get('is_public') is not None,
    )
    db.session.add(persona)
    db.session.commit()
    flash(f'角色「{persona.name}」创建成功', 'success')
    return redirect(url_for('user.ai_friend') + '#personas')


@user_bp.route('/ai_friend/personas/<int:persona_id>/edit', methods=['POST'])
def ai_friend_persona_edit(persona_id):
    if not session.get('user_id'):
        return redirect(url_for('auth.login'))
    persona = get_or_404(AIPersona, persona_id)
    if persona.user_id != session['user_id']:
        abort(403)

    data, errors = _validate_persona(request.form)
    if errors:
        flash('；'.join(errors), 'danger')
        return redirect(url_for('user.ai_friend') + '#personas')

    persona.name = data['name']
    persona.source = data['source'] or None
    persona.personality = data['personality']
    persona.speech_style = data['speech_style']
    persona.catchphrases = data['catchphrases'] or None
    persona.address_user = data['address_user'] or None
    persona.is_public = request.form.get('is_public') is not None
    persona.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
    db.session.commit()
    flash('角色已更新', 'success')
    return redirect(url_for('user.ai_friend') + '#personas')


@user_bp.route('/ai_friend/personas/<int:persona_id>/delete', methods=['POST'])
def ai_friend_persona_delete(persona_id):
    if not session.get('user_id'):
        return redirect(url_for('auth.login'))
    persona = get_or_404(AIPersona, persona_id)
    if persona.user_id != session['user_id']:
        abort(403)
    db.session.delete(persona)
    db.session.commit()
    flash('角色已删除', 'success')
    return redirect(url_for('user.ai_friend') + '#personas')


@user_bp.route('/api/ai_friend/fill', methods=['POST'])
def ai_friend_fill():
    """「✨ 帮我补全」：用免费模型按角色名生成人设草稿（不落库，用户可改）"""
    if not session.get('user_id'):
        return jsonify({'ok': False, 'error': '请先登录'}), 401

    payload = request.get_json(silent=True) or {}
    name = (payload.get('name') or '').strip()
    hint = (payload.get('hint') or '').strip()
    if not name:
        return jsonify({'ok': False, 'error': '先填写角色名'}), 400
    if ai_friend_mod.check_input(name + ' ' + hint):
        return jsonify({'ok': False, 'error': '这个角色名不在允许范围内'}), 400

    draft = ai_friend_mod.fill_persona(name, hint)
    if not draft:
        return jsonify({'ok': False, 'error': 'AI 补全暂时不可用，请手动填写'}), 503
    return jsonify({'ok': True, 'draft': draft})


@user_bp.route('/api/ai_friend/chat', methods=['POST'])
def ai_friend_chat():
    """聊天接口：免费模型池 → FAQ 兜底，永不调用付费模型"""
    if not session.get('user_id'):
        return jsonify({'ok': False, 'error': '请先登录'}), 401
    user_id = session['user_id']

    payload = request.get_json(silent=True) or {}
    message = (payload.get('message') or '').strip()
    if not message:
        return jsonify({'ok': False, 'error': '消息不能为空'}), 400
    if len(message) > ai_friend_mod.MAX_MESSAGE_LEN:
        return jsonify({'ok': False, 'error': '消息太长了，精简到 %d 字以内吧'
                        % ai_friend_mod.MAX_MESSAGE_LEN}), 400

    used = AIChatMessage.query.filter(
        AIChatMessage.user_id == user_id,
        AIChatMessage.role == 'user',
        AIChatMessage.created_at >= _cn_today_start(),
    ).count()
    limit = ai_friend_mod.daily_limit()
    if used >= limit:
        return jsonify({
            'ok': True,
            'reply': ai_friend_mod.faq_answer(message),
            'fallback': True,
            'model': None,
            'reason': 'quota',
            'notice': f'今天的 AI 对话次数已用完（{limit} 条/天），下面先按常见问题回答',
            'used': used,
            'limit': limit,
        })

    persona = _select_persona(payload.get('persona_id'), user_id)
    persona_dict = None
    if persona is not None:
        persona_dict = {'name': persona.name, 'source': persona.source,
                        'personality': persona.personality,
                        'speech_style': persona.speech_style,
                        'catchphrases': persona.catchphrases,
                        'address_user': persona.address_user}

    history_q = AIChatMessage.query.filter_by(user_id=user_id)
    if persona is None:
        history_q = history_q.filter(AIChatMessage.persona_id.is_(None))
    else:
        history_q = history_q.filter(AIChatMessage.persona_id == persona.id)
    history_rows = history_q.order_by(AIChatMessage.created_at.desc()).limit(6).all()
    history = [{'role': r.role, 'content': r.content} for r in reversed(history_rows)]

    result = ai_friend_mod.chat(message, persona=persona_dict, history=history)

    db.session.add(AIChatMessage(user_id=user_id, persona_id=persona.id if persona else None,
                                 role='user', content=message))
    db.session.add(AIChatMessage(user_id=user_id, persona_id=persona.id if persona else None,
                                 role='assistant', content=result['reply'],
                                 model=result.get('model'),
                                 is_fallback=bool(result.get('fallback'))))
    db.session.commit()

    return jsonify({
        'ok': True,
        'reply': result['reply'],
        'fallback': bool(result.get('fallback')),
        'model': result.get('model'),
        'reason': result.get('reason'),
        'notice': ('AI 暂时不可用，先按站内常见问题回答你～'
                   if result.get('fallback') else None),
        'persona': persona.name if persona else '小动漫',
        'used': used + 1,
        'limit': limit,
    })


# ========== 追番清单 ==========
ANIME_STATUSES = {'want': '想看', 'watching': '在看', 'done': '看过'}


@user_bp.route('/my/anime_list')
def anime_list():
    """我的追番：按状态筛选，卡片可直接改状态/评分/移除"""
    if not session.get('user_id'):
        return redirect(url_for('auth.login'))

    status = request.args.get('status', 'all')
    query = AnimeListEntry.query.filter_by(user_id=session['user_id'])
    if status in ANIME_STATUSES:
        query = query.filter_by(status=status)
    entries = query.order_by(AnimeListEntry.updated_at.desc()).all()

    counts = dict(
        db.session.query(AnimeListEntry.status, func.count(AnimeListEntry.id))
        .filter(AnimeListEntry.user_id == session['user_id'])
        .group_by(AnimeListEntry.status).all()
    )
    total = sum(counts.values())
    return render_template('anime_list.html', entries=entries, status=status,
                           counts=counts, total=total, labels=ANIME_STATUSES)


@user_bp.route('/api/anime_list', methods=['POST'])
def api_anime_list_add():
    """把番剧加入追番清单 / 更新状态（已存在则改状态，不重复插入）"""
    if not session.get('user_id'):
        return jsonify({'ok': False, 'error': '请先登录'}), 401

    data = request.get_json(silent=True) or {}
    try:
        bangumi_id = int(data.get('bangumi_id'))
    except (TypeError, ValueError):
        return jsonify({'ok': False, 'error': 'bangumi_id 无效'}), 400

    title = (data.get('title') or '').strip()[:200]
    if not title:
        return jsonify({'ok': False, 'error': '缺少番剧名'}), 400
    status = data.get('status') or 'want'
    if status not in ANIME_STATUSES:
        status = 'want'
    image = (data.get('image') or '').strip()[:500] or None

    entry = AnimeListEntry.query.filter_by(user_id=session['user_id'],
                                           bangumi_id=bangumi_id).first()
    if entry:
        entry.title = title
        entry.image = image or entry.image
        entry.status = status
    else:
        entry = AnimeListEntry(user_id=session['user_id'], bangumi_id=bangumi_id,
                               title=title, image=image, status=status)
        db.session.add(entry)
    db.session.commit()
    return jsonify({'ok': True, 'id': entry.id, 'status': entry.status,
                    'label': ANIME_STATUSES.get(entry.status, '')})


@user_bp.route('/api/anime_list/<int:entry_id>', methods=['POST'])
def api_anime_list_update(entry_id):
    """更新状态/评分，或移除（action=delete）"""
    if not session.get('user_id'):
        return jsonify({'ok': False, 'error': '请先登录'}), 401

    entry = db.session.get(AnimeListEntry, entry_id)
    if entry is None or entry.user_id != session['user_id']:
        return jsonify({'ok': False, 'error': '记录不存在'}), 404

    data = request.get_json(silent=True) or {}
    action = data.get('action')
    if action == 'delete':
        db.session.delete(entry)
        db.session.commit()
        return jsonify({'ok': True, 'deleted': True})

    if data.get('status') in ANIME_STATUSES:
        entry.status = data['status']

    rating = data.get('rating')
    if rating is not None:
        try:
            rating = int(rating)
        except (TypeError, ValueError):
            rating = None
        entry.rating = rating if rating and 1 <= rating <= 10 else None

    db.session.commit()
    return jsonify({'ok': True, 'id': entry.id, 'status': entry.status,
                    'rating': entry.rating})
