"""论坛 / 举报相关公开路由（从 public.py 拆出，共用 public 蓝图）。

endpoint 名保持不变：public.board / public.board_new / public.board_thread /
public.add_reply / public.edit_thread / public.edit_reply / public.delete_thread /
public.delete_reply / public.report_content。
"""
from datetime import datetime, timedelta, timezone

from flask import abort, flash, redirect, render_template, request, session, url_for
from sqlalchemy import func
from sqlalchemy.orm import joinedload

from .models import db, User, Message, Reply, Notification, Photo, Report
from .notify import notify
from .public_bp import public_bp
from .utils import cn_now, to_cn


def _tpl_cn_time(dt):
    """naive UTC → 东八区展示；None 原样返回。"""
    return to_cn(dt)


BOARD_PAGE_SIZE = 20
BOARD_REPLY_PAGE_SIZE = 30      # 帖子详情里每页楼层数（热门帖不一次性拉全部回复）
BOARD_TITLE_MAX = 120
BOARD_CONTENT_MAX = 5000


def _tpl_cn_time(dt):
    # 库里存的是 naive UTC，展示统一转东八区
    return dt + timedelta(hours=8) if dt else dt


@public_bp.route('/board')
def board():
    # 论坛首页：主题帖列表，按「最后活跃」倒序分页
    page = max(1, request.args.get('page', 1, type=int) or 1)

    stats = db.session.query(
        Reply.message_id.label('mid'),
        func.count(Reply.id).label('reply_count'),
        func.max(Reply.timestamp).label('last_ts'),
    ).group_by(Reply.message_id).subquery()

    last_activity = func.coalesce(stats.c.last_ts, Message.timestamp)
    total = db.session.query(func.count(Message.id)).scalar() or 0
    rows = (
        db.session.query(Message, stats.c.reply_count, last_activity.label('last_activity'))
        .options(joinedload(Message.user))
        .outerjoin(stats, stats.c.mid == Message.id)
        .order_by(last_activity.desc(), Message.id.desc())
        .offset((page - 1) * BOARD_PAGE_SIZE)
        .limit(BOARD_PAGE_SIZE)
        .all()
    )

    threads = [{
        'id': msg.id,
        'title': msg.display_title,
        'author': msg.user.username if msg.user else msg.nickname,
        'created': _tpl_cn_time(msg.timestamp),
        'last_activity': _tpl_cn_time(last_ts or msg.timestamp),
        'reply_count': int(reply_count or 0),
    } for msg, reply_count, last_ts in rows]

    total_pages = max(1, (total + BOARD_PAGE_SIZE - 1) // BOARD_PAGE_SIZE)
    return render_template('board.html', threads=threads, page=page,
                           total=total, total_pages=total_pages)


@public_bp.route('/board', methods=['POST'])
def board_legacy_post():
    # 旧版留言板表单的兼容入口：统一引导到论坛发帖页
    if not session.get('user_id'):
        return redirect(url_for('auth.login'))
    flash('留言板已升级为论坛，请补个标题再发帖', 'info')
    return redirect(url_for('public.board_new'))


@public_bp.route('/board/new', methods=['GET', 'POST'])
def board_new():
    # 发帖必须登录：原先只靠全局拦截器兜着，爬虫 UA 旁路可伪造，能匿名发帖
    if not session.get('user_id'):
        return redirect(url_for('auth.login'))

    form = {'title': '', 'content': ''}
    if request.method == 'POST':
        title = (request.form.get('title') or '').strip()
        content = (request.form.get('content') or '').strip()
        form = {'title': title, 'content': content}
        if len(title) < 2:
            flash('标题至少 2 个字', 'danger')
        elif len(title) > BOARD_TITLE_MAX:
            flash(f'标题不能超过 {BOARD_TITLE_MAX} 个字', 'danger')
        elif not content:
            flash('正文不能为空', 'danger')
        elif len(content) > BOARD_CONTENT_MAX:
            flash(f'正文不能超过 {BOARD_CONTENT_MAX} 字', 'danger')
        else:
            user_id = session['user_id']
            # 防重复提交：同一用户 5 秒内发相同正文，直接回到已发出的那一帖
            cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=5)
            duplicate = Message.query.filter(
                Message.user_id == user_id,
                Message.content == content,
                Message.timestamp >= cutoff
            ).first()
            if duplicate:
                return redirect(url_for('public.board_thread', thread_id=duplicate.id))
            msg = Message(
                title=title,
                content=content,
                nickname=session.get('username', '匿名'),
                user_id=user_id,
            )
            db.session.add(msg)
            db.session.commit()
            flash('发帖成功', 'success')
            return redirect(url_for('public.board_thread', thread_id=msg.id))
    return render_template('board_new.html', form=form,
                           title_max=BOARD_TITLE_MAX, content_max=BOARD_CONTENT_MAX)


@public_bp.route('/board/<int:thread_id>')
def board_thread(thread_id):
    # 帖子详情：主楼 + 分页楼层，底部固定输入框可直接回复本主题
    thread = db.session.get(Message, thread_id)
    if not thread:
        abort(404)

    page = max(1, request.args.get('page', 1, type=int) or 1)
    reply_total = Reply.query.filter_by(message_id=thread_id).count()
    total_pages = max(1, (reply_total + BOARD_REPLY_PAGE_SIZE - 1) // BOARD_REPLY_PAGE_SIZE)
    page = min(page, total_pages)
    reply_offset = (page - 1) * BOARD_REPLY_PAGE_SIZE

    replies = (
        Reply.query.options(
            joinedload(Reply.user),
            joinedload(Reply.parent_reply).joinedload(Reply.user),
        )
        .filter_by(message_id=thread_id)
        .order_by(Reply.timestamp.asc(), Reply.id.asc())
        .offset(reply_offset)
        .limit(BOARD_REPLY_PAGE_SIZE)
        .all()
    )

    parent_names = {}
    for reply in replies:
        reply.timestamp = _tpl_cn_time(reply.timestamp)
        if reply.parent_reply_id:
            parent = reply.parent_reply
            if parent is None:
                parent_names[reply.id] = '已删除的楼层'
            else:
                parent_names[reply.id] = parent.user.username if parent.user else parent.nickname

    # 权限：作者本人可编辑/删除自己的内容；运营/站长可删除任意内容（管理操作），
    # 但不再对别人的内容显示「编辑」——否则站长会看到满屏编辑按钮，也容易误改社员原话。
    uid = session.get('user_id')
    me = db.session.get(User, uid) if uid else None
    is_moderator = bool(me and (me.is_staff or me.is_owner))
    is_author_thread = bool(uid and thread.user_id == uid)
    can_delete_thread = bool(uid and (is_moderator or is_author_thread))
    editable_reply_ids = {r.id for r in replies if uid and r.user_id == uid}
    deletable_reply_ids = {
        r.id for r in replies if uid and (is_moderator or r.user_id == uid)
    }

    return render_template(
        'board_thread.html',
        thread=thread,
        thread_time=_tpl_cn_time(thread.timestamp),
        replies=replies,
        parent_names=parent_names,
        reply_count=reply_total,
        reply_offset=reply_offset,
        page=page,
        total_pages=total_pages,
        is_moderator=is_moderator,
        is_author_thread=is_author_thread,
        can_delete_thread=can_delete_thread,
        editable_reply_ids=editable_reply_ids,
        deletable_reply_ids=deletable_reply_ids,
    )


@public_bp.route('/reply/<int:message_id>', methods=['POST'])
def add_reply(message_id):
    if not session.get('user_id'):
        return redirect(url_for('auth.login'))

    thread = db.session.get(Message, message_id)
    if not thread:
        abort(404)

    nickname = session.get('username', '匿名')
    content = (request.form.get('content') or '').strip()
    parent_reply_id = request.form.get('parent_reply_id')

    if len(content) > BOARD_CONTENT_MAX:
        flash(f'回复不能超过 {BOARD_CONTENT_MAX} 字', 'danger')
        return redirect(url_for('public.board_thread', thread_id=message_id))

    if content:
        user_id = session.get('user_id')
        # 防重复提交：同一用户 5 秒内发相同内容到同一主题，忽略
        cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=5)
        duplicate = Reply.query.filter(
            Reply.user_id == user_id,
            Reply.message_id == message_id,
            Reply.content == content,
            Reply.timestamp >= cutoff
        ).first()
        if not duplicate:
            reply = Reply(
                nickname=nickname,
                content=content,
                message_id=message_id,
                user_id=user_id,
                parent_reply_id=int(parent_reply_id) if parent_reply_id else None
            )
            db.session.add(reply)
            db.session.commit()

            # 通知被回复的人（回复楼层 → 通知层主；回复主楼 → 通知楼主）
            target_user_id = None
            if parent_reply_id:
                parent = db.session.get(Reply, int(parent_reply_id))
                if parent and parent.user_id and parent.user_id != user_id:
                    target_user_id = parent.user_id
            elif thread.user_id and thread.user_id != user_id:
                target_user_id = thread.user_id

            if target_user_id:
                notify(
                    target_user_id,
                    f'{nickname} 回复了你',
                    content[:50] + ('...' if len(content) > 50 else ''),
                    notify_type='reply',
                    link=url_for('public.board_thread', thread_id=message_id)
                )
                db.session.commit()

    return redirect(url_for('public.board_thread', thread_id=message_id))


def _can_delete_board_item(author_id):
    """作者本人可删自己的帖子/回复；运营（staff）/站长（owner）可删任意。"""
    uid = session.get('user_id')
    if not uid:
        return False
    if author_id == uid:
        return True
    user = db.session.get(User, uid)
    return bool(user and (user.is_staff or user.is_owner))


@public_bp.route('/board/<int:thread_id>/delete', methods=['POST'])
def delete_thread(thread_id):
    """删除主题帖（连带回复与指向它的通知）"""
    if not session.get('user_id'):
        return redirect(url_for('auth.login'))

    thread = db.session.get(Message, thread_id)
    if not thread:
        abort(404)
    if not _can_delete_board_item(thread.user_id):
        abort(403)

    Reply.query.filter_by(message_id=thread_id).delete(synchronize_session=False)
    # 站内通知里的链接指向该帖子，帖子没了就一起清掉，避免点进去 404
    Notification.query.filter(
        Notification.link.like(f'%/board/{thread_id}')).delete(synchronize_session=False)
    db.session.delete(thread)
    db.session.commit()
    flash('帖子已删除', 'success')
    return redirect(url_for('public.board'))


@public_bp.route('/reply/<int:reply_id>/delete', methods=['POST'])
def delete_reply(reply_id):
    """删除单条回复；它下面的子回复改为顶层，避免引用到不存在的楼层"""
    if not session.get('user_id'):
        return redirect(url_for('auth.login'))

    reply = db.session.get(Reply, reply_id)
    if not reply:
        abort(404)
    if not _can_delete_board_item(reply.user_id):
        abort(403)

    thread_id = reply.message_id
    page = max(1, request.form.get('page', 1, type=int) or 1)
    Reply.query.filter_by(parent_reply_id=reply.id).update(
        {'parent_reply_id': None}, synchronize_session=False)
    db.session.delete(reply)
    db.session.commit()
    flash('回复已删除', 'success')
    return redirect(url_for('public.board_thread', thread_id=thread_id, page=page))


@public_bp.route('/board/<int:thread_id>/edit', methods=['GET', 'POST'])
def edit_thread(thread_id):
    """编辑主题帖：作者本人 / 运营 / 站长"""
    if not session.get('user_id'):
        return redirect(url_for('auth.login'))

    thread = db.session.get(Message, thread_id)
    if not thread:
        abort(404)
    if not _can_delete_board_item(thread.user_id):
        abort(403)

    form = {'title': thread.title or '', 'content': thread.content}
    if request.method == 'POST':
        title = (request.form.get('title') or '').strip()
        content = (request.form.get('content') or '').strip()
        form = {'title': title, 'content': content}
        if len(title) < 2:
            flash('标题至少 2 个字', 'danger')
        elif len(title) > BOARD_TITLE_MAX:
            flash(f'标题不能超过 {BOARD_TITLE_MAX} 个字', 'danger')
        elif not content:
            flash('正文不能为空', 'danger')
        elif len(content) > BOARD_CONTENT_MAX:
            flash(f'正文不能超过 {BOARD_CONTENT_MAX} 字', 'danger')
        else:
            thread.title = title
            thread.content = content
            thread.edited_at = datetime.now(timezone.utc).replace(tzinfo=None)
            db.session.commit()
            flash('帖子已更新', 'success')
            return redirect(url_for('public.board_thread', thread_id=thread.id))

    return render_template('board_edit.html', thread=thread, form=form,
                           title_max=BOARD_TITLE_MAX, content_max=BOARD_CONTENT_MAX)


@public_bp.route('/reply/<int:reply_id>/edit', methods=['POST'])
def edit_reply(reply_id):
    """编辑单条回复：作者本人 / 运营 / 站长"""
    if not session.get('user_id'):
        return redirect(url_for('auth.login'))

    reply = db.session.get(Reply, reply_id)
    if not reply:
        abort(404)
    if not _can_delete_board_item(reply.user_id):
        abort(403)

    page = max(1, request.form.get('page', 1, type=int) or 1)
    content = (request.form.get('content') or '').strip()
    if not content:
        flash('回复不能为空', 'danger')
    elif len(content) > BOARD_CONTENT_MAX:
        flash(f'回复不能超过 {BOARD_CONTENT_MAX} 字', 'danger')
    else:
        reply.content = content
        reply.edited_at = datetime.now(timezone.utc).replace(tzinfo=None)
        db.session.commit()
        flash('回复已更新', 'success')
    return redirect(url_for('public.board_thread', thread_id=reply.message_id, page=page))


REPORT_REASONS = ('广告/刷屏', '辱骂/攻击', '色情/不适内容', '泄露隐私', '其他')
REPORT_TARGETS = ('thread', 'reply', 'photo')


def _report_target(ttype, tid):
    """按类型取被举报对象，返回 (对象, 预览文本, 来源链接)"""
    if ttype == 'thread':
        obj = db.session.get(Message, tid)
        if obj:
            return obj, obj.display_title, url_for('public.board_thread', thread_id=obj.id)
    elif ttype == 'reply':
        obj = db.session.get(Reply, tid)
        if obj:
            return obj, obj.content[:120], url_for('public.board_thread', thread_id=obj.message_id)
    elif ttype == 'photo':
        obj = db.session.get(Photo, tid)
        if obj:
            return obj, obj.filename, url_for('public.gallery')
    return None, '', url_for('public.board')


@public_bp.route('/report', methods=['GET', 'POST'])
def report_content():
    """举报帖子 / 回复 / 照片，进入管理后台审核队列"""
    if not session.get('user_id'):
        return redirect(url_for('auth.login'))

    ttype = request.values.get('type', '')
    tid = request.values.get('id', type=int)
    if ttype not in REPORT_TARGETS or not tid:
        flash('举报目标无效', 'danger')
        return redirect(url_for('public.board'))

    target, preview, back_url = _report_target(ttype, tid)
    if target is None:
        flash('举报目标不存在或已被删除', 'danger')
        return redirect(back_url)
    if getattr(target, 'user_id', None) == session['user_id']:
        flash('不能举报自己发布的内容', 'info')
        return redirect(back_url)

    if request.method == 'POST':
        reason = request.form.get('reason', '')
        detail = (request.form.get('detail') or '').strip()[:300]
        if reason not in REPORT_REASONS:
            flash('请选择举报原因', 'danger')
        else:
            dup = Report.query.filter_by(reporter_id=session['user_id'],
                                         target_type=ttype, target_id=tid,
                                         status='pending').first()
            if dup:
                flash('你已经举报过这条内容，管理员正在处理', 'info')
            else:
                db.session.add(Report(reporter_id=session['user_id'], target_type=ttype,
                                      target_id=tid, reason=reason, detail=detail or None))
                db.session.commit()
                flash('举报已提交，管理员会尽快处理', 'success')
            return redirect(back_url)

    return render_template('report.html', target_type=ttype, target_id=tid,
                           preview=preview, reasons=REPORT_REASONS, back_url=back_url)


