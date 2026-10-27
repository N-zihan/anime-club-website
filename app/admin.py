"""
动漫社官网 · 管理后台模块
====================================

本模块为社团管理层提供完整的后台管理功能，包含以下子系统：

1. 活动管理 —— 增删改社团活动，支持活动关联照片的级联删除
2. 照片墙管理 —— 上传/删除活动照片，存储至 Supabase Storage
3. 番剧资源管理 —— 审核社员推荐的番剧，或手动添加资源
4. 用户管理 —— 查看社员列表，切换运营/站长身份，删除用户

权限体系：
- admin_required 装饰器：允许站长或运营访问
- owner_required 装饰器：仅允许站长访问（用于敏感操作）

后台入口：
- /admin/entry 统一入口，根据角色自动分流至站长或运营面板
"""

import io
import logging
import re
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from functools import wraps

from flask import Blueprint, render_template, request, redirect, url_for, session, flash
from flask import send_file
from openpyxl import Workbook
from openpyxl.styles import Font, Alignment

from .models import (db, User, Activity, Photo, Message, Reply,
                     Contest, Nomination, Candidate, ContestVote, Notification,
                     AIPersona, AIChatMessage, AdminLog, Report)
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import joinedload

from .utils import get_supabase, allowed_file, compress_image, get_or_404, cn_now
from .notify import notify
from .auth import send_reset_email, EMAIL_PATTERN
from .contest_engine import calc_stage_times
from .config import STAGE_DAYS, PHOTO_COMPRESS_SIZE, COMPRESS_QUALITY

TOTAL_CONTEST_DAYS = sum(STAGE_DAYS.values())

admin_bp = Blueprint('admin', __name__)


# ---------- 权限装饰器 ----------
def _current_role():
    """每次请求都用数据库现算角色。

    session['user_role'] 只是登录那一刻的快照：撤销运营/站长权限、甚至删除用户，
    都不会让已发出的会话失效。原实现只读 session，等于撤权不生效。
    """
    user_id = session.get('user_id')
    if not user_id:
        return None
    user = db.session.get(User, user_id)
    if not user:
        session.clear()
        return None
    if user.is_owner:
        return 'owner'
    if user.is_staff:
        return 'staff'
    return 'member'


def admin_required(f):
    """运营或站长均可访问"""

    @wraps(f)
    def decorated_function(*args, **kwargs):
        if _current_role() not in ('owner', 'staff'):
            flash('你没有管理权限', 'danger')
            return redirect(url_for('public.index'))
        return f(*args, **kwargs)

    return decorated_function


def owner_required(f):
    """仅站长可访问"""

    @wraps(f)
    def decorated_function(*args, **kwargs):
        if _current_role() != 'owner':
            flash('该功能仅站长可用', 'danger')
            return redirect(url_for('admin.dashboard'))
        return f(*args, **kwargs)

    return decorated_function


# ---------- 统一入口 ----------
@admin_bp.route('/admin/entry')
def admin_entry():
    role = _current_role()
    if role == 'owner':
        return redirect(url_for('admin.dashboard'))
    if role == 'staff':
        return redirect(url_for('admin.staff_dashboard'))
    flash('你没有管理权限', 'danger')
    return redirect(url_for('public.index'))


# ---------- 站长后台 ----------
@admin_bp.route('/admin/dashboard')
@admin_required
def dashboard():
    return render_template('admin_dashboard.html')


# ---------- 运营后台 ----------
@admin_bp.route('/staff/dashboard')
@admin_required
def staff_dashboard():
    return render_template('staff_dashboard.html')


# ---------- 活动管理 ----------
@admin_bp.route('/admin/activities')
@admin_required
def admin_activities():
    activities = Activity.query.order_by(Activity.date.desc()).all()
    return render_template('admin_activities.html', activities=activities)


@admin_bp.route('/admin/activities/add', methods=['GET', 'POST'])
@admin_required
def admin_activity_add():
    if request.method == 'POST':
        title = request.form.get('title', '').strip()
        date = request.form.get('date', '').strip()
        content = request.form.get('content', '').strip()

        if not title or not date or not content:
            flash('标题、日期和内容不能为空', 'danger')
            return redirect(url_for('admin.admin_activity_add'))

        # 上限校验：超长会直接撞数据库列宽报 500（title 100 / date 20）
        if len(title) > 100 or len(date) > 20:
            flash('标题不超过100字、日期格式无效', 'danger')
            return redirect(url_for('admin.admin_activity_add'))

        new_activity = Activity(title=title, date=date, content=content)
        db.session.add(new_activity)
        db.session.commit()
        flash('活动添加成功', 'success')
        return redirect(url_for('admin.admin_activities'))
    return render_template('admin_activity_form.html', activity=None)


@admin_bp.route('/admin/activities/edit/<int:id>', methods=['GET', 'POST'])
@admin_required
def admin_activity_edit(id):
    activity = get_or_404(Activity, id)
    if request.method == 'POST':
        title = request.form.get('title', '').strip()
        date = request.form.get('date', '').strip()
        content = request.form.get('content', '').strip()

        if not title or not date or not content:
            flash('标题、日期和内容不能为空', 'danger')
            return redirect(url_for('admin.admin_activity_edit', id=id))

        activity.title = title
        activity.date = date
        activity.content = content
        db.session.commit()
        flash('活动已更新', 'success')
        return redirect(url_for('admin.admin_activities'))
    return render_template('admin_activity_form.html', activity=activity)


@admin_bp.route('/admin/activities/delete/<int:id>', methods=['POST'])
@admin_required
def admin_activity_delete(id):
    supabase = get_supabase()
    activity = get_or_404(Activity, id)

    # 先删除该活动关联的所有照片（文件 + 数据库记录）
    for photo in activity.photos:
        try:
            supabase.storage.from_('photos').remove([photo.filename])
        except Exception:
            pass  # 云文件删不掉也继续，至少删数据库记录
        db.session.delete(photo)

    db.session.delete(activity)
    db.session.commit()
    flash('活动及其所有照片已删除', 'success')
    return redirect(url_for('admin.admin_activities'))


# ---------- 照片墙管理 ----------
@admin_bp.route('/admin/gallery')
@admin_required
def admin_gallery():
    supabase = get_supabase()
    activities = Activity.query.order_by(Activity.date.desc()).all()
    photos = Photo.query.order_by(Photo.upload_time.desc()).all()
    for photo in photos:
        photo.url = supabase.storage.from_('photos').get_public_url(photo.filename)
    return render_template('admin_gallery.html', activities=activities, photos=photos)


@admin_bp.route('/admin/gallery/upload', methods=['POST'])
@admin_required
def admin_gallery_upload():
    supabase = get_supabase()

    if 'file' not in request.files:
        flash('没有文件', 'danger')
        return redirect(url_for('admin.admin_gallery'))
    file = request.files['file']
    if file.filename == '':
        flash('未选择文件', 'danger')
        return redirect(url_for('admin.admin_gallery'))

    activity_id = request.form.get('activity_id')
    if activity_id:
        try:
            activity_id = int(activity_id)
        except (TypeError, ValueError):
            activity_id = None
        if activity_id is not None and db.session.get(Activity, activity_id) is None:
            activity_id = None

    if file and allowed_file(file.filename):
        ext = file.filename.rsplit('.', 1)[1].lower()
        filename = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}.{ext}"

        try:
            raw_data = file.read()
            # 照片墙压缩到 1200x1200，品质85
            compressed = compress_image(raw_data, max_size=PHOTO_COMPRESS_SIZE, quality=COMPRESS_QUALITY)
            supabase.storage.from_('photos').upload(
                filename,
                compressed,
                file_options={"content-type": 'image/jpeg'}
            )
        except Exception as e:
            flash(f'上传失败: {str(e)}', 'danger')
            return redirect(url_for('admin.admin_gallery'))

        photo = Photo(
            filename=filename,
            activity_id=activity_id if activity_id else None,
            uploader=session.get('username', '社员')  # 记录上传者
        )
        db.session.add(photo)
        db.session.commit()
        flash('照片上传成功', 'success')
    else:
        flash('不支持的文件类型', 'danger')
    return redirect(url_for('admin.admin_gallery'))


@admin_bp.route('/admin/gallery/delete/<int:photo_id>', methods=['POST'])
@admin_required
def admin_gallery_delete(photo_id):
    """删除单张照片（文件 + 数据库记录）"""
    supabase = get_supabase()
    photo = get_or_404(Photo, photo_id)

    # 从 Supabase Storage 删除文件
    try:
        supabase.storage.from_('photos').remove([photo.filename])
    except Exception as e:
        # 云文件删不掉也继续，至少删数据库记录
        flash(f'云存储文件删除失败（但数据库记录已删）: {str(e)}', 'warning')

    db.session.delete(photo)
    db.session.commit()

    flash('照片已删除', 'success')
    return redirect(url_for('admin.admin_gallery'))


# ---------- 用户管理 ----------
@admin_bp.route('/admin/users')
@admin_required
@owner_required
def admin_users():
    users = User.query.order_by(User.registered_at.desc()).all()
    return render_template('admin_users.html', users=users)


@admin_bp.route('/admin/users/delete/<int:user_id>', methods=['POST'])
@admin_required
@owner_required
def admin_delete_user(user_id):
    user = get_or_404(User, user_id)
    if user.id == session.get('user_id'):
        flash('不能删除自己', 'danger')
        return redirect(url_for('admin.admin_users'))

    # 级联删除所有关联数据
    # 注意：删除顺序很重要，先删子表，再删主表
    try:
        # 0. 先解除别人对该用户的引用，否则 PostgreSQL 的外键会拦下删除（原先直接 500）
        Contest.query.filter_by(created_by=user.id).update(
            {'created_by': None}, synchronize_session=False)

        nomination_ids = [n.id for n in Nomination.query.filter_by(user_id=user.id).all()]
        if nomination_ids:
            Candidate.query.filter(
                Candidate.nomination_id.in_(nomination_ids)
            ).update({'nomination_id': None}, synchronize_session=False)

        reply_ids = [r.id for r in Reply.query.filter_by(user_id=user.id).all()]
        if reply_ids:
            Reply.query.filter(
                Reply.parent_reply_id.in_(reply_ids)
            ).update({'parent_reply_id': None}, synchronize_session=False)

        # 1. 删除该用户的提名（Nomination）
        Nomination.query.filter_by(user_id=user.id).delete(synchronize_session=False)

        # 2. 删除该用户的投票（ContestVote）
        ContestVote.query.filter_by(user_id=user.id).delete(synchronize_session=False)

        # 3. 删除该用户的回复（作为作者）
        Reply.query.filter_by(user_id=user.id).delete(synchronize_session=False)

        # 4. 删除该用户的留言及这些留言下的所有回复（含他人回复）
        for msg in Message.query.filter_by(user_id=user.id).all():
            Reply.query.filter_by(message_id=msg.id).delete(synchronize_session=False)
        Message.query.filter_by(user_id=user.id).delete(synchronize_session=False)

        # 5. 删除该用户的通知
        Notification.query.filter_by(user_id=user.id).delete(synchronize_session=False)

        # 6. 最后删除用户
        db.session.delete(user)
        db.session.commit()
        flash('用户及其所有关联数据已删除', 'success')
    except IntegrityError:
        db.session.rollback()
        flash('删除失败：该用户仍有关联数据，请先处理其创建的赛事。', 'danger')

    return redirect(url_for('admin.admin_users'))


@admin_bp.route('/admin/users/toggle_staff/<int:user_id>', methods=['POST'])
@admin_required
@owner_required
def admin_toggle_staff(user_id):
    user = get_or_404(User, user_id)
    user.is_staff = not user.is_staff
    db.session.commit()
    flash(f'用户 {user.username} 的运营状态已更新', 'success')
    return redirect(url_for('admin.admin_users'))


@admin_bp.route('/admin/users/toggle_owner/<int:user_id>', methods=['POST'])
@admin_required
@owner_required
def admin_toggle_owner(user_id):
    user = get_or_404(User, user_id)

    # 目标已是站长 → 不允许取消（社团必须始终保留一位站长）
    if user.is_owner:
        flash('站长不能取消自己，请直接转移给其他成员', 'warning')
        return redirect(url_for('admin.admin_users'))

    # 转移站长：先清空所有人的站长标记，再指定新站长
    User.query.update({User.is_owner: False})
    user.is_owner = True
    db.session.commit()

    # 刷新当前请求发起者的 session 角色（原站长此时已失去 owner）
    current_id = session.get('user_id')
    if current_id == user.id:
        session['user_role'] = 'owner'
    else:
        current = db.session.get(User, current_id)
        if current:
            if current.is_owner:
                session['user_role'] = 'owner'
            elif current.is_staff:
                session['user_role'] = 'staff'
            else:
                session['user_role'] = 'member'

    flash(f'已将站长转移给 {user.username}', 'success')
    return redirect(url_for('admin.admin_users'))


# ---------- 友好闭环：站长代绑邮箱 / 后台发送重置链接 ----------
@admin_bp.route('/admin/users/bind_email/<int:user_id>', methods=['POST'])
@admin_required
@owner_required
def admin_bind_email(user_id):
    """站长直接为用户绑定邮箱（无需验证码，站长身份即授权）。

    解决「未绑定邮箱又忘了密码、无法自助找回」的社员困局。
    """
    user = get_or_404(User, user_id)
    email = (request.form.get('email') or '').strip().lower()
    if not email or not re.match(EMAIL_PATTERN, email):
        flash('邮箱格式无效', 'danger')
        return redirect(url_for('admin.admin_users'))
    if User.query.filter(User.email == email, User.id != user_id).first():
        flash('该邮箱已被其他账号绑定', 'danger')
        return redirect(url_for('admin.admin_users'))
    user.email = email
    db.session.commit()
    notify(user.id, '站长已为你绑定邮箱',
           '以后你可以在「个人设置」或「忘记密码」中通过该邮箱找回密码。',
           notify_type='system', send_mail=False)
    # notify() 只把站内通知 add 进会话、不提交；这里必须显式提交，
    # 否则请求结束时会话回滚，站内通知永远不落库（邮件是同步发的，不受影响）。
    db.session.commit()
    flash(f'已为 {user.username} 绑定 {email}', 'success')
    return redirect(url_for('admin.admin_users'))


@admin_bp.route('/admin/users/reset_password/<int:user_id>', methods=['POST'])
@admin_required
@owner_required
def admin_reset_password(user_id):
    """站长后台直接给指定用户发一条密码重置链接（发送至其邮箱）。"""
    user = get_or_404(User, user_id)
    if not user.email:
        flash(f'用户 {user.username} 尚未绑定邮箱，请先为其绑定再发送重置链接', 'warning')
        return redirect(url_for('admin.admin_users'))
    token = secrets.token_urlsafe(32)
    user.reset_token = token
    user.reset_token_expires = datetime.now() + timedelta(hours=1)
    db.session.commit()
    reset_link = url_for('auth.reset_password', token=token, _external=True)
    if send_reset_email(user.email, reset_link):
        flash(f'重置链接已发送至 {user.email}', 'success')
    else:
        flash('邮件发送失败，请检查 SMTP 配置', 'danger')
    notify(user.id, '站长已发送密码重置链接',
           '请登录邮箱查收重置链接；如果没收到，请联系站长。',
           notify_type='system', send_mail=False)
    return redirect(url_for('admin.admin_users'))


@admin_bp.route('/admin/users/export')
@admin_required
@owner_required
def export_users_excel():
    users = User.query.order_by(User.registered_at.desc()).all()

    wb = Workbook()
    ws = wb.active
    ws.title = '社员名单'

    # 表头
    headers = ['ID', '用户名', 'QQ号', '注册时间', '运营', '站长']
    for col, header in enumerate(headers, 1):
        cell = ws.cell(row=1, column=col, value=header)
        cell.font = Font(bold=True)
        cell.alignment = Alignment(horizontal='center')

    # 数据行
    for row_idx, user in enumerate(users, 2):
        ws.cell(row=row_idx, column=1, value=user.id)
        ws.cell(row=row_idx, column=2, value=user.username)
        ws.cell(row=row_idx, column=3, value=user.qq)
        ws.cell(row=row_idx, column=4, value=user.registered_at.strftime('%Y-%m-%d %H:%M'))
        ws.cell(row=row_idx, column=5, value='是' if user.is_staff else '否')
        ws.cell(row=row_idx, column=6, value='是' if user.is_owner else '否')

    # 列宽自适应
    for col in range(1, 7):
        max_length = 0
        for row in range(1, len(users) + 2):
            cell_value = ws.cell(row=row, column=col).value
            if cell_value:
                max_length = max(max_length, len(str(cell_value)))
        ws.column_dimensions[chr(64 + col)].width = min(max_length + 2, 30)  # A=65, B=66, ...

    # 写入内存
    output = io.BytesIO()
    wb.save(output)
    output.seek(0)

    return send_file(
        output,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        as_attachment=True,
        download_name=f'社员名单_{datetime.now().strftime("%Y%m%d")}.xlsx'
    )


# ---------- 留言管理 ----------
@admin_bp.route('/admin/messages')
@admin_required
def admin_messages():
    """留言管理页面，显示所有留言及回复。

    旧实现会在循环里逐条访问 msg.replies / reply.user（每条一次 SQL），15 条留言
    就要 16+ 次查询；Supabase pooler 慢的时候容易把请求拖到 Vercel 60 秒上限，
    表现为「帖子管理点不开、弹 500 页面」。现在一次取齐留言、一次取齐回复再分组。
    也不再原地修改 ORM 对象的 timestamp（只用于展示，改对象可能触发自动 flush）。
    """
    messages = (Message.query
                .options(joinedload(Message.user))
                .order_by(Message.timestamp.desc()).all())
    msg_ids = [msg.id for msg in messages]
    replies = []
    if msg_ids:
        replies = (Reply.query
                   .options(joinedload(Reply.user))
                   .filter(Reply.message_id.in_(msg_ids))
                   .order_by(Reply.timestamp.asc()).all())

    replies_by_msg = {}
    for reply in replies:
        replies_by_msg.setdefault(reply.message_id, []).append(reply)

    def _cn(dt):
        return dt + timedelta(hours=8) if dt else None

    rows = []
    for msg in messages:
        msg_replies = replies_by_msg.get(msg.id, [])
        rows.append({
            'msg': msg,
            'time': _cn(msg.timestamp),
            'replies': [{'reply': reply, 'time': _cn(reply.timestamp)}
                        for reply in msg_replies],
            'reply_count': len(msg_replies),
        })
    return render_template('admin_messages.html', rows=rows)


@admin_bp.route('/admin/messages/delete/<int:message_id>', methods=['POST'])
@admin_required
def admin_delete_message(message_id):
    """删除留言及其所有回复"""
    message = get_or_404(Message, message_id)
    # 删除所有回复（如果有外键级联，可以直接删除message，但为了明确，手动删除回复）
    for reply in message.replies.all():
        db.session.delete(reply)
    db.session.delete(message)
    db.session.commit()
    flash('留言及其所有回复已删除', 'success')
    return redirect(url_for('admin.admin_messages'))


@admin_bp.route('/admin/replies/delete/<int:reply_id>', methods=['POST'])
@admin_required
def admin_delete_reply(reply_id):
    """删除单条回复，保留留言和其他回复"""
    reply = get_or_404(Reply, reply_id)
    db.session.delete(reply)
    db.session.commit()
    flash('回复已删除', 'success')
    return redirect(url_for('admin.admin_messages'))


# ========== 萌战系统 · 管理后台 ==========

@admin_bp.route('/admin/contests/manage')
@admin_required
def admin_contests_manage():
    contests = Contest.query.order_by(Contest.created_at.desc()).all()
    return render_template('admin_contests_manage.html', contests=contests)


@admin_bp.route('/admin/contests/create', methods=['GET', 'POST'])
@admin_required
def admin_contest_create():
    if request.method == 'POST':
        title = request.form.get('title')
        description = request.form.get('description')
        gender_mode = request.form.get('gender_mode', 'separate')
        open_at_str = request.form.get('open_at')

        if not title or not description or not open_at_str:
            flash('标题、描述和开始时间不能为空', 'danger')
            return redirect(url_for('admin.admin_contest_create'))

        open_at = datetime.strptime(open_at_str, '%Y-%m-%dT%H:%M')

        contest = Contest(
            title=title,
            description=description,
            type='saimoe',
            gender_mode=gender_mode,
            status='draft',
            created_by=session.get('user_id'),
            open_at=open_at,
            # 用真实赛程终点（第 50 天的 18:00），而不是 open_at + 50 天。
            # 否则「进行中」列表会在真实结束前 8~22 小时就把赛事移走。
            close_at=calc_stage_times(open_at)['final_result_end'],
        )
        db.session.add(contest)
        db.session.commit()
        flash(f'赛事 "{title}" 创建成功', 'success')
        return redirect(url_for('admin.admin_contest_edit', contest_id=contest.id))

    return render_template('admin_contest_create.html', total_days=TOTAL_CONTEST_DAYS)


@admin_bp.route('/admin/contests/edit/<int:contest_id>', methods=['GET', 'POST'])
@admin_required
def admin_contest_edit(contest_id):
    contest = get_or_404(Contest, contest_id)

    if request.method == 'POST':
        contest.title = request.form.get('title')
        contest.description = request.form.get('description')
        contest.gender_mode = request.form.get('gender_mode', 'separate')
        contest.status = request.form.get('status')

        open_at_str = request.form.get('open_at')
        if open_at_str:
            contest.open_at = datetime.strptime(open_at_str, '%Y-%m-%dT%H:%M')
            contest.close_at = calc_stage_times(contest.open_at)['final_result_end']

        old_status = contest.status
        db.session.commit()
        if old_status != 'open' and contest.status == 'open':
            _notify_contest_open(contest)
        flash('赛事信息已更新', 'success')
        return redirect(url_for('admin.admin_contest_edit', contest_id=contest.id))

    pending_nominations = contest.nominations.filter_by(status='pending').all()
    candidates = contest.candidates.all()
    return render_template('admin_contest_edit.html',
                           contest=contest,
                           nominations=pending_nominations,
                           candidates=candidates,
                           total_days=TOTAL_CONTEST_DAYS)


@admin_bp.route('/admin/contests/delete/<int:contest_id>', methods=['POST'])
@admin_required
def admin_contest_delete(contest_id):
    contest = get_or_404(Contest, contest_id)

    try:
        # 0. 先删投票：contest_votes 同时外键引用 contests / candidates，
        #    不清理的话 PostgreSQL 会直接抛外键错误（原先没有任何处理）
        ContestVote.query.filter_by(contest_id=contest.id).delete(synchronize_session=False)

        # 1. 手动删除所有关联的候选角色 (Candidates)
        #    使用 delete() 直接执行 SQL，比循环删除更高效
        Candidate.query.filter_by(contest_id=contest.id).delete(synchronize_session=False)

        # 2. 手动删除所有关联的提名 (Nominations)
        Nomination.query.filter_by(contest_id=contest.id).delete(synchronize_session=False)

        # 3. 最后删除赛事本身
        db.session.delete(contest)
        db.session.commit()
        flash('赛事已删除', 'success')
    except IntegrityError:
        db.session.rollback()
        flash('删除失败：赛事仍有关联数据，请先清理提名/投票。', 'danger')

    return redirect(url_for('admin.admin_contests_manage'))


# ---------- 提名审核 ----------
@admin_bp.route('/admin/nominations/approve/<int:nomination_id>', methods=['POST'])
@admin_required
def admin_nomination_approve(nomination_id):
    nomination = get_or_404(Nomination, nomination_id)
    # 幂等/防重放：对已处理过的提名重复 POST 会把状态改成 rejected，
    # 造成候选池与提名状态不一致。
    if nomination.status != 'pending':
        flash('该提名已处理过，无需重复操作', 'warning')
        return redirect(url_for('admin.admin_contest_edit', contest_id=nomination.contest_id))
    contest = nomination.contest

    existing = Candidate.query.filter_by(
        contest_id=contest.id,
        name=nomination.name
    ).first()
    if existing:
        flash(f'角色 "{nomination.name}" 已存在于候选池', 'danger')
        nomination.status = 'rejected'
        db.session.commit()
        return redirect(url_for('admin.admin_contest_edit', contest_id=contest.id))

    candidate = Candidate(
        contest_id=contest.id,
        nomination_id=nomination.id,
        name=nomination.name,
        source=nomination.source,
        gender=nomination.gender,
        image_url=nomination.image_url,
        description=nomination.description,
        stage='pending'  # 初始状态
    )
    db.session.add(candidate)
    nomination.status = 'approved'
    db.session.commit()
    notify(
        nomination.user_id,
        f'你的提名「{nomination.name}」已通过',
        '角色已进入候选池，快去看看吧！',
        notify_type='audit',
        link=url_for('public.contest_detail', contest_id=contest.id)
    )
    db.session.commit()
    flash(f'已通过提名: {nomination.name}', 'success')
    return redirect(url_for('admin.admin_contest_edit', contest_id=contest.id))


@admin_bp.route('/admin/nominations/reject/<int:nomination_id>', methods=['POST'])
@admin_required
def admin_nomination_reject(nomination_id):
    nomination = get_or_404(Nomination, nomination_id)
    # 已通过的提名不能被"拒绝"掉：原先会删掉提名行、候选却仍在池中，
    # 用户还被告知"名额已退回"，重新提名后必然再被拒，名额白白作废。
    if nomination.status != 'pending':
        flash('该提名已处理过，无需重复操作', 'warning')
        return redirect(url_for('admin.admin_contest_edit', contest_id=nomination.contest_id))
    contest_id = nomination.contest_id
    user_id = nomination.user_id
    name = nomination.name

    # 直接删除，释放名额、放开名字
    db.session.delete(nomination)
    db.session.commit()

    notify(
        user_id,
        f'你的提名「{name}」被拒绝',
        '名额已退回，可以重新提名其他角色。',
        notify_type='audit',
    )
    db.session.commit()
    flash(f'已拒绝提名: {name}', 'warning')
    return redirect(url_for('admin.admin_contest_edit', contest_id=contest_id))


@admin_bp.route('/admin/candidates/delete/<int:candidate_id>', methods=['POST'])
@admin_required
def admin_candidate_delete(candidate_id):
    candidate = get_or_404(Candidate, candidate_id)
    contest_id = candidate.contest_id
    nomination = candidate.nomination

    try:
        # 先删该角色的投票记录，否则 PostgreSQL 外键会拦下删除
        ContestVote.query.filter_by(candidate_id=candidate.id).delete(synchronize_session=False)

        # 如果有关联的提名，撤回它的"已通过"状态，并通知用户
        if nomination:
            notify(
                nomination.user_id,
                f'你的提名「{nomination.name}」已被移除',
                '名额已退回。',
                notify_type='audit',
            )
            db.session.delete(nomination)

        db.session.delete(candidate)
        db.session.commit()
        flash('已从候选池移除该角色', 'success')
    except IntegrityError:
        db.session.rollback()
        flash('移除失败：该角色仍有关联数据。', 'danger')

    return redirect(url_for('admin.admin_contest_edit', contest_id=contest_id))


@admin_bp.route('/admin/candidates/ban/<int:candidate_id>', methods=['POST'])
@admin_required
def admin_candidate_ban(candidate_id):
    """对候选角色实施禁赛（禁止被投票），直至被淘汰或手动恢复。"""
    candidate = get_or_404(Candidate, candidate_id)
    candidate.banned = True
    db.session.commit()
    flash(f'已禁赛角色「{candidate.name}」，禁赛期间禁止投票', 'success')
    return redirect(url_for('admin.admin_contest_edit', contest_id=candidate.contest_id))


@admin_bp.route('/admin/candidates/unban/<int:candidate_id>', methods=['POST'])
@admin_required
def admin_candidate_unban(candidate_id):
    """解除候选角色的禁赛状态。"""
    candidate = get_or_404(Candidate, candidate_id)
    candidate.banned = False
    db.session.commit()
    flash(f'已解除角色「{candidate.name}」的禁赛', 'success')
    return redirect(url_for('admin.admin_contest_edit', contest_id=candidate.contest_id))


@admin_bp.route('/admin/notifications/send', methods=['GET', 'POST'])
@admin_required
def admin_notification_send():
    if request.method == 'POST':
        title = request.form.get('title')
        content = request.form.get('content')
        if title:
            from .notify import notify_all # pylint: disable=import-outside-toplevel
            notify_all(title, content, notify_type='system')
            db.session.commit()
            flash('通知已发送给全体社员', 'success')
            return redirect(url_for('admin.admin_notification_send'))
    return render_template('admin_notification_send.html')


# ========== AI 虚拟好友管理 ==========
@admin_bp.route('/admin/ai_friend')
@admin_required
def admin_ai_friend():
    """AI 好友：用量概览 + 人设审核（下架/恢复）"""
    now_cn = cn_now()
    today_start = now_cn.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(hours=8)

    personas = AIPersona.query.order_by(AIPersona.created_at.desc()).limit(200).all()
    model_rows = db.session.query(
        AIChatMessage.model, func.count(AIChatMessage.id)
    ).filter(AIChatMessage.role == 'assistant').group_by(AIChatMessage.model).all()

    stats = {
        'personas': AIPersona.query.count(),
        'public_personas': AIPersona.query.filter_by(is_public=True).count(),
        'hidden_personas': AIPersona.query.filter_by(status='hidden').count(),
        'messages_today': AIChatMessage.query.filter(
            AIChatMessage.created_at >= today_start).count(),
    }
    return render_template('admin_ai_friend.html', personas=personas, stats=stats,
                           model_rows=model_rows)


@admin_bp.route('/admin/ai_friend/personas/<int:persona_id>/toggle', methods=['POST'])
@admin_required
def admin_ai_friend_toggle(persona_id):
    """下架 / 恢复用户自定义人设"""
    persona = get_or_404(AIPersona, persona_id)
    if persona.status == 'hidden':
        persona.status = 'active'
        flash(f'已恢复角色「{persona.name}」', 'success')
    else:
        persona.status = 'hidden'
        flash(f'已下架角色「{persona.name}」', 'success')
    db.session.commit()
    return redirect(url_for('admin.admin_ai_friend'))


# ========== 操作日志：自动记录后台写操作 ==========
_ADMIN_ACTION_LABELS = {
    '/admin/messages/delete/<int:message_id>': '删除帖子',
    '/admin/replies/delete/<int:reply_id>': '删除回复',
    '/admin/users/delete/<int:user_id>': '删除用户',
    '/admin/users/toggle_staff/<int:user_id>': '切换运营权限',
    '/admin/users/toggle_owner/<int:user_id>': '切换站长权限',
    '/admin/users/bind_email/<int:user_id>': '绑定邮箱',
    '/admin/users/reset_password/<int:user_id>': '重置密码',
    '/admin/activities/add': '新增活动',
    '/admin/activities/edit/<int:id>': '编辑活动',
    '/admin/activities/delete/<int:id>': '删除活动',
    '/admin/gallery/upload': '上传照片',
    '/admin/gallery/delete/<int:photo_id>': '删除照片',
    '/admin/contests/create': '创建赛事',
    '/admin/contests/edit/<int:contest_id>': '编辑赛事',
    '/admin/contests/delete/<int:contest_id>': '删除赛事',
    '/admin/nominations/approve/<int:nomination_id>': '通过提名',
    '/admin/nominations/reject/<int:nomination_id>': '驳回提名',
    '/admin/candidates/delete/<int:candidate_id>': '删除候选角色',
    '/admin/candidates/ban/<int:candidate_id>': '禁赛角色',
    '/admin/candidates/unban/<int:candidate_id>': '解除禁赛',
    '/admin/notifications/send': '群发通知',
    '/admin/ai_friend/personas/<int:persona_id>/toggle': '审核 AI 人设',
}


@admin_bp.after_request
def log_admin_action(response):
    """后台的 POST 成功操作自动留痕（谁、对什么、做了什么、从哪来）。

    放在 after_request 里而不是每条路由手写，新增后台路由也能自动被记录
    （没有专门中文名的路由会退回记录路径本身）。
    """
    try:
        if request.method != 'POST' or not session.get('user_id'):
            return response
        if response.status_code >= 400:
            return response
        me = db.session.get(User, session['user_id'])
        if me is None or not (me.is_staff or me.is_owner):
            return response
        rule = request.url_rule.rule if request.url_rule else request.path
        if not rule.startswith('/admin/'):
            return response
        action = _ADMIN_ACTION_LABELS.get(rule, rule)
        target = ', '.join(f'{k}={v}' for k, v in (request.view_args or {}).items())
        detail = (request.form.get('content') or request.form.get('title')
                  or request.form.get('description') or '')[:300]
        db.session.add(AdminLog(
            user_id=session['user_id'],
            username=session.get('username', ''),
            action=action,
            target=(target[:120] or None),
            detail=(detail or request.path)[:500],
            ip=request.remote_addr,
        ))
        db.session.commit()
    except Exception:  # 日志失败绝不能影响正常操作
        db.session.rollback()
    return response


@admin_bp.route('/admin/logs')
@admin_required
def admin_logs():
    """管理员操作日志（分页）"""
    page = max(1, request.args.get('page', 1, type=int) or 1)
    per_page = 50
    total = AdminLog.query.count()
    total_pages = max(1, (total + per_page - 1) // per_page)
    page = min(page, total_pages)
    logs = (AdminLog.query
            .order_by(AdminLog.created_at.desc(), AdminLog.id.desc())
            .offset((page - 1) * per_page)
            .limit(per_page)
            .all())
    return render_template('admin_logs.html', logs=logs, page=page,
                           total=total, total_pages=total_pages)


def _notify_contest_open(contest):
    """赛事进入 open 状态时给全体社员发一条 contest 通知（失败不影响主流程）。"""
    try:
        link = url_for('public.contest_detail', contest_id=contest.id)
        users = User.query.filter(User.id.isnot(None)).all()
        for u in users:
            notify(u.id, f'{contest.title} 开赛啦',
                   '提名/投票入口已开放，来赛事中心看看吧',
                   notify_type='contest', link=link)
        db.session.commit()
    except Exception as exc:  # pragma: no cover - 通知失败不影响开赛
        db.session.rollback()
        log = logging.getLogger(__name__)
        log.warning('开赛通知发送失败: %s', exc)


# ========== 举报审核队列 ==========
def _report_target_preview(report):
    """返回被举报内容的预览与链接（目标可能已被删除）。"""
    if report.target_type == 'thread':
        obj = db.session.get(Message, report.target_id)
        if obj:
            return obj.display_title, url_for('public.board_thread', thread_id=obj.id)
    elif report.target_type == 'reply':
        obj = db.session.get(Reply, report.target_id)
        if obj:
            return obj.content[:120], url_for('public.board_thread', thread_id=obj.message_id)
    elif report.target_type == 'photo':
        obj = db.session.get(Photo, report.target_id)
        if obj:
            return obj.filename, url_for('public.gallery')
    return '（内容已被删除）', None


@admin_bp.route('/admin/reports')
@admin_required
def admin_reports():
    """举报审核队列：默认只看待处理"""
    status = request.args.get('status', 'pending')
    if status not in ('pending', 'resolved', 'dismissed', 'all'):
        status = 'pending'
    page = max(1, request.args.get('page', 1, type=int) or 1)
    per_page = 30

    query = Report.query
    if status != 'all':
        query = query.filter_by(status=status)
    total = query.count()
    total_pages = max(1, (total + per_page - 1) // per_page)
    page = min(page, total_pages)
    reports = (query.order_by(Report.created_at.desc(), Report.id.desc())
               .offset((page - 1) * per_page).limit(per_page).all())

    rows = [{
        'report': r,
        'preview': _report_target_preview(r)[0],
        'link': _report_target_preview(r)[1],
        'reporter': r.reporter.username if r.reporter else '已注销',
    } for r in reports]
    pending_total = Report.query.filter_by(status='pending').count()
    return render_template('admin_reports.html', rows=rows, status=status,
                           page=page, total=total, total_pages=total_pages,
                           pending_total=pending_total)


@admin_bp.route('/admin/reports/<int:report_id>/handle', methods=['POST'])
@admin_required
def admin_report_handle(report_id):
    """处理举报：删除内容并处理 / 仅标记已处理 / 忽略"""
    report = get_or_404(Report, report_id)
    action = request.form.get('action', 'resolve')

    note = ''
    if action == 'delete':
        if report.target_type == 'thread':
            obj = db.session.get(Message, report.target_id)
            if obj:
                Reply.query.filter_by(message_id=obj.id).delete(synchronize_session=False)
                Notification.query.filter(
                    Notification.link.like(f'%/board/{obj.id}')).delete(synchronize_session=False)
                db.session.delete(obj)
        elif report.target_type == 'reply':
            obj = db.session.get(Reply, report.target_id)
            if obj:
                Reply.query.filter_by(parent_reply_id=obj.id).update(
                    {'parent_reply_id': None}, synchronize_session=False)
                db.session.delete(obj)
        elif report.target_type == 'photo':
            obj = db.session.get(Photo, report.target_id)
            if obj:
                try:
                    get_supabase().storage.from_('photos').remove([obj.filename])
                except Exception:  # pragma: no cover - 存储删除失败不阻塞
                    pass
                db.session.delete(obj)
        report.status = 'resolved'
        note = '已删除内容'
    elif action == 'dismiss':
        report.status = 'dismissed'
        note = '举报忽略'
    else:
        report.status = 'resolved'
        note = '标记已处理'

    report.handled_by = session.get('user_id')
    report.handled_at = datetime.now(timezone.utc).replace(tzinfo=None)
    report.note = note
    db.session.commit()
    flash(f'举报 #{report_id} 已处理（{note}）', 'success')
    return redirect(url_for('admin.admin_reports'))
