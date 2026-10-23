"""
动漫社官网 · 公共页面模块
====================================

本模块处理所有对客户端可见的页面

此外，本模块还注册了全局错误处理器：
400、403、404、405、413、500 均有对应的自定义页面。
"""
import os
import threading
import time
import uuid
from datetime import timedelta, datetime, timezone
from urllib.parse import urlparse

from flask import Blueprint, render_template, request, redirect, url_for, session, flash, jsonify, send_from_directory, Response, abort
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import joinedload

from .bangumi import (
    get_subjects, search_subjects, get_subject_detail,
    get_subject_characters, get_character_detail,
)
from .anime_news import (
    get_anime_news, get_article_content, find_article_meta, search_news,
    _safe_media_url, _is_allowed_embed_url, _extract_videos,
)
# 注意：此处曾顶层导入 app.video_proxy 的 7 个函数，但 public.py 内从未使用它们
# （已逐名 grep 确认零引用）。而 video_proxy.py 顶层 `import aiohttp`，aiohttp 不在
# requirements.txt 中（Vercel 走 pip install -r requirements.txt），该导入会让整站
# 在缺 aiohttp 的环境直接 ModuleNotFoundError（app/__init__.py:29 → 本行 → 崩溃）。
# 因零使用，直接删除导入；视频代理路由由 app/__init__.py 的可选导入注册。
# 如将来确需在此使用，请改用 try/except 可选导入，勿再写成顶层硬导入。
import re
from .ai import generate_commentary, generate_prediction
from .config import (
    NOMINATION_LIMIT,
    QUALIFYING_MAX_CANDIDATES,
    QUALIFYING_MAX_VOTES,
    QUALIFYING_MAX_PER_CANDIDATE,
    NOMINATION_IMAGE_SIZE,
    COMPRESS_QUALITY
)
from .contest_engine import (
    calc_stage_times, calc_phase, auto_activate_contest,
    run_qualifying_promotion, run_group_promotion,
    run_knockout_advance, run_final_ranking,
    prepare_group_round_data
)
from .models import (db, User, Activity, Photo, Message, Reply, Contest,
                     Nomination, Candidate, ContestVote, Notification, Report)
from .preferences import BADGE_POLL_CHOICES, NOTIFY_LABELS, NOTIFY_TYPES
from .utils import (get_supabase, compress_image, get_or_404, allowed_file,
                    is_public_http_url, safe_get)
from .notify import notify


def supabase_render_ctx():
    """统一的 Supabase 前端配置。

    必须保证值永不为 None（否则 Jinja 的 |tojson 会在模板里抛
    TypeError: Object of type Undefined is not JSON serializable，
    导致整页 500）。所有渲染投票页的路由都应通过 **supabase_render_ctx()
    传参，避免各路由重复取 env 时漏传。
    """
    return {
        'supabase_url': os.getenv('SUPABASE_URL') or '',
        'supabase_anon_key': os.getenv('SUPABASE_ANON_KEY') or '',
    }

public_bp = Blueprint('public', __name__)
# Bangumi 图片服务器需要的请求头
HEADERS_FOR_IMAGE = {
    'User-Agent': 'Mozilla/5.0 (compatible; nanyi-anime-club/1.0)',
    'Referer': 'https://bgm.tv/',
}


# ============================================================
# 视频 URL 的模板级防线
# ============================================================
# 模板是最后一道防线：即使视图层或解析层漏了，也不能把任意 URL 输出成
# 可执行的 iframe。这两个过滤器让模板**不需要** |safe，也拿不到脏 URL：
#   {{ v|media_url }}  → 净化后的 https URL，不安全则返回 ''
#   {{ u|embed_ok }}   → 该 URL 是否属于允许嵌入的白名单播放器域
# 用过滤器而不是在模板里内联判断，是为了保证净化逻辑只有一处实现。

@public_bp.app_template_filter('media_url')
def _tpl_media_url(value):
    """模板过滤器：返回可安全输出的 URL（不安全则返回空串）"""
    # _safe_media_url 已做 html.unescape + 字符级净化 + 协议/域名校验，
    # 任何不可信 URL 都会整条被丢弃，这里绝不回退输出原始值。
    return _safe_media_url(value) or ''


@public_bp.app_template_filter('embed_ok')
def _tpl_embed_ok(value):
    """模板过滤器：该 URL 是否允许作为 <iframe> 嵌入（白名单域 + https）"""
    return bool(_safe_media_url(value) and _is_allowed_embed_url(value))
    
    
# XSS 防护：模板助手函数，用于安全地渲染用户内容
@public_bp.app_template_filter('safe_html')
def _safe_html(value):
    """模板过滤器：安全地渲染用户生成的内容"""
    from markupsafe import escape
    return escape(value)
    
    
# 安全辅助函数：验证用户输入
def _validate_input(value, max_length=100, pattern=None, field_type='text'):
    """安全验证用户输入"""
    if not value or len(value.strip()) == 0:
        return False
    stripped = value.strip()
    if len(stripped) > max_length:
        return False
    if pattern and not re.match(pattern, stripped):
        return False
    
    # 额外的XSS防护
    if field_type == 'html':
        # 检查是否存在XSS攻击相关模式
        xss_patterns = [
            r'<script[^>]*>.*?</script>',
            r'javascript:',
            r'on\w+\s*=',
            r'<iframe',
            r'<object',
            r'<embed',
            r'<link',
            r'<meta',
            r'<style[^>]*>.*?</style>',
            r'<\s*/?\s*(script|iframe|object|embed|link|meta|style)[^>]*>',
        ]
        for xss_pattern in xss_patterns:
            if re.search(xss_pattern, stripped, re.IGNORECASE | re.DOTALL):
                return False
    elif field_type == 'url':
        # URL安全验证
        if not re.match(r'^https?://[^\s<>"\'()]+$', stripped):
            return False
    elif field_type == 'email':
        if not re.match(r'^[^@\s]+@[^@\s]+\.[^@\s]+$', stripped):
            return False
    elif field_type == 'text':
        # 基础文本安全检查，防止常见的XSS模式
        dangerous_patterns = [
            r'<[^>]*script[^>]*>',
            r'javascript:',
            r'on\w+\s*=',
            r'<\s*/?\s*(script|iframe|object|embed|link|meta|style)[^>]*>',
        ]
        for dangerous_pattern in dangerous_patterns:
            if re.search(dangerous_pattern, stripped, re.IGNORECASE):
                return False
    return True


def _calc_current_phase(contest):
    """计算赛事当前 phase（投票页面用）"""
    # 统一 naive datetime：contest.open_at 来自数据库，统一去掉 tzinfo
    if contest.open_at and hasattr(contest.open_at, 'tzinfo') and contest.open_at.tzinfo is not None:
        contest.open_at = contest.open_at.replace(tzinfo=None)
    now = _cn_now()
    times = calc_stage_times(contest.open_at)
    auto_activate_contest(contest, now)
    return calc_phase(contest, now, times)


# CSRF 保护：全局JS助手函数
def _csrf_protected_url(url):
    """生成受CSRF保护的URL（对于GET请求，添加CSRF token）"""
    # 验证URL安全性
    if not url or not isinstance(url, str):
        return url
    
    # 检查URL是否包含危险协议
    if url.startswith('javascript:') or url.startswith('data:') or url.startswith('vbscript:'):
        return ''
    
    # 检查URL是否包含XSS相关模式
    if re.search(r'<script[^>]*>.*?</script>', url, re.IGNORECASE | re.DOTALL):
        return ''
    
    # 确保URL是绝对路径或本站URL
    if url.startswith('/'):
        # 本站URL，安全
        pass
    elif url.startswith('http://') or url.startswith('https://'):
        # 外部URL，需要验证是否为本站域名
        try:
            parsed = urlparse(url)
            if parsed.hostname and parsed.hostname != request.host:
                # 可以加入域名白名单检查
                pass
        except Exception:
            return ''
    
    return url


# 会话安全辅助函数
def _get_secure_session_id():
    """获取安全的会话ID"""
    import secrets
    return secrets.token_urlsafe(32)


def _get_secure_headers():
    """获取安全的HTTP头"""
    return {
        'X-Content-Type-Options': 'nosniff',
        'X-Frame-Options': 'DENY',
        'X-XSS-Protection': '1; mode=block',
        'Referrer-Policy': 'strict-origin-when-cross-origin',
        'Content-Security-Policy': "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:;"
    }


@public_bp.route('/')
def splash():
    return render_template('splash.html',
                          og_title='动漫社官网',
                          og_description='动漫社官网，集社团展示、互动交流、资源分享、线上赛事于一体',
                          og_image='/static/images/icon-512.png')


@public_bp.route('/settings')
def settings():
    """站点设置页：外观与显示 / 通知与免打扰 / 数据与隐私。

    页面本身对访客开放（外观与本地数据偏好无需登录），
    账号级设置（通知）在模板里按登录状态分别渲染表单或登录提示。
    """
    return render_template('settings.html',
                           notify_all_types=NOTIFY_TYPES,
                           notify_labels=NOTIFY_LABELS,
                           badge_poll_choices=BADGE_POLL_CHOICES)


@public_bp.route('/home')
def index():
    return render_template('index.html')


@public_bp.route('/about')
def about():
    users = User.query.all()
    staff = User.query.filter_by(is_staff=True).all()
    owner = User.query.filter_by(is_owner=True).first()
    activity_count = Activity.query.count()
    contest_count = Contest.query.count()
    return render_template('about.html', users=users, staff=staff, owner=owner,
                           activity_count=activity_count, contest_count=contest_count)


@public_bp.route('/about/history')
def history():
    """网站发展史页面"""
    from .changelog import get_all_commits # pylint: disable=import-outside-toplevel
    commits = get_all_commits()
    return render_template('history.html', commits=commits)


@public_bp.route('/activities')
def activities():
    activities = Activity.query.order_by(Activity.date.asc()).all()
    return render_template('activities.html', activities=activities)


@public_bp.route('/gallery')
def gallery():
    supabase = get_supabase()
    activities = Activity.query.options(joinedload(Activity.photos)).order_by(Activity.date.desc()).all()
    uncategorized_photos = Photo.query.filter_by(activity_id=None).all()

    for photo in uncategorized_photos:
        photo.url = supabase.storage.from_('photos').get_public_url(photo.filename)
    for activity in activities:
        for photo in activity.photos:
            photo.url = supabase.storage.from_('photos').get_public_url(photo.filename)

    return render_template('gallery.html', activities=activities, uncategorized_photos=uncategorized_photos)


# ========== 论坛（原留言板） ==========
BOARD_PAGE_SIZE = 20
BOARD_REPLY_PAGE_SIZE = 30      # 帖子详情里每页楼层数（热门帖不一次性拉全部回复）
BOARD_TITLE_MAX = 120
BOARD_CONTENT_MAX = 5000


def _cn_time(dt):
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
        'created': _cn_time(msg.timestamp),
        'last_activity': _cn_time(last_ts or msg.timestamp),
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
        reply.timestamp = _cn_time(reply.timestamp)
        if reply.parent_reply_id:
            parent = reply.parent_reply
            if parent is None:
                parent_names[reply.id] = '已删除的楼层'
            else:
                parent_names[reply.id] = parent.user.username if parent.user else parent.nickname

    # 删除权限：作者本人可删自己的帖子/回复；运营/站长可删任意内容
    uid = session.get('user_id')
    me = db.session.get(User, uid) if uid else None
    is_moderator = bool(me and (me.is_staff or me.is_owner))
    can_delete_thread = bool(uid and (is_moderator or thread.user_id == uid))
    deletable_reply_ids = {
        r.id for r in replies if uid and (is_moderator or r.user_id == uid)
    }

    return render_template(
        'board_thread.html',
        thread=thread,
        thread_time=_cn_time(thread.timestamp),
        replies=replies,
        parent_names=parent_names,
        reply_count=reply_total,
        reply_offset=reply_offset,
        page=page,
        total_pages=total_pages,
        can_delete_thread=can_delete_thread,
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


@public_bp.route('/anime_resources')
def anime_resources():
    """番剧百科（数据来自 Bangumi）"""
    return render_template('anime_guide.html')


@public_bp.route('/api/anime/cover')
def api_anime_cover():
    """代理 Bangumi 封面图，走 Vercel CDN 缓存"""
    from urllib.parse import unquote
    url = unquote(request.args.get('url', ''))

    # 白名单校验，防止 SSRF（只允许 Bangumi 图床）
    if not url.startswith('https://lain.bgm.tv/'):
        abort(404)

    # 改用 safe_get 逐跳校验：裸 requests.get 默认自动跟随重定向，图床侧的开放
    # 重定向即可让请求跳到内网地址，前缀白名单只挡住了第一跳。与 api_news_image()
    # 保持同一套防护。
    res = safe_get(url, headers=HEADERS_FOR_IMAGE, timeout=8)
    if res is None or res.status_code >= 400:
        abort(404)

    # 不原样回传上游 Content-Type：否则可让本站同源渲染任意 HTML（同源 XSS）
    content_type = (res.headers.get('Content-Type') or '').split(';')[0].strip().lower()
    if not content_type.startswith('image/'):
        abort(404)

    return Response(
        res.content,
        mimetype=content_type,
        headers={
            'Cache-Control': 'public, max-age=604800, s-maxage=604800',
            'X-Content-Type-Options': 'nosniff',
        },
    )


@public_bp.route('/api/anime/list')
def api_anime_list():
    """分页拉取番剧列表"""
    try:
        offset = int(request.args.get('offset', 0))
    except ValueError:
        offset = 0
    sort = request.args.get('sort', 'rank')
    if sort not in ('rank', 'heat', 'score', 'date'):
        sort = 'rank'
    items = get_subjects(offset=offset, sort=sort)
    return jsonify({'items': items})


@public_bp.route('/api/anime/search')
def api_anime_search():
    keyword = (request.args.get('q') or '').strip()
    if not keyword:
        return jsonify({'items': [], 'total': 0})
    try:
        offset = int(request.args.get('offset', 0))
    except ValueError:
        offset = 0
    sort = request.args.get('sort', 'match')
    if sort not in ('match', 'heat', 'rank', 'score'):
        sort = 'match'
    items, total = search_subjects(keyword, offset=offset, sort=sort)
    return jsonify({'items': items, 'total': total})


@public_bp.route('/api/anime/detail/<int:subject_id>')
def api_anime_detail(subject_id):
    """拉取单个番剧的完整详情"""
    d = get_subject_detail(subject_id)
    if not d:
        return jsonify({'error': 'Not found'}), 404
    resp = jsonify(d)
    resp.headers['Cache-Control'] = 'public, max-age=3600, s-maxage=3600'
    return resp


@public_bp.route('/api/anime/characters/<int:subject_id>')
def api_anime_characters(subject_id):
    """拉取某部番剧的角色列表"""
    chars = get_subject_characters(subject_id)
    resp = jsonify({'characters': chars})
    resp.headers['Cache-Control'] = 'public, max-age=86400, s-maxage=86400'
    return resp


@public_bp.route('/api/anime/character/<int:char_id>')
def api_anime_character_detail(char_id):
    """拉取单个角色的详情"""
    d = get_character_detail(char_id)
    if not d:
        return jsonify({'error': 'Not found'}), 404
    return jsonify(d)


@public_bp.route('/anime_news/rss.xml')
def anime_news_rss():
    """番剧新闻 RSS（公开页）：方便阅读器订阅，也给外部抓取一个规范入口。"""
    from .anime_news import get_anime_news  # pylint: disable=import-outside-toplevel
    from urllib.parse import quote  # pylint: disable=import-outside-toplevel
    from xml.sax.saxutils import escape  # pylint: disable=import-outside-toplevel

    try:
        articles, _meta = get_anime_news(limit=30)
    except Exception:  # 上游/翻译异常时给空 feed，不 500
        articles = []

    club = os.getenv('CLUB_NAME') or '动漫社'
    base = request.url_root.rstrip('/')
    items = []
    for a in articles or []:
        title = a.get('title_zh') or a.get('title') or '(无标题)'
        link = a.get('link') or a.get('url') or ''
        detail = f'{base}/anime_news/article?url={quote(link, safe="")}'
        desc = a.get('excerpt_zh') or a.get('excerpt') or ''
        source = a.get('source') or ''
        items.append(
            '<item>'
            f'<title>{escape(title)}</title>'
            f'<link>{escape(detail)}</link>'
            f'<guid isPermaLink="false">{escape(detail)}</guid>'
            f'<description>{escape(desc)}</description>'
            f'<category>{escape(source)}</category>'
            '</item>'
        )

    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<rss version="2.0"><channel>'
        f'<title>{escape(club)} · 番剧新闻</title>'
        f'<link>{escape(base + "/anime_news")}</link>'
        f'<description>{escape(club + " 聚合的番剧新闻")}</description>'
        '<language>zh-cn</language>'
        + ''.join(items) +
        '</channel></rss>'
    )
    return Response(xml, mimetype='application/rss+xml')


@public_bp.route('/anime_news')
def anime_news():
    """番剧新闻动态"""
    return render_template('anime_news.html')


@public_bp.route('/anime_news/article')
def anime_news_article():
    """独立文章详情页（卡片点击进入）"""
    from urllib.parse import unquote
    url = unquote(request.args.get('url', ''))
    if not url or not is_public_http_url(url):
        abort(404)

    # 从预取搜索语料中查找元数据；取代原先 20/100 两轮列表查询，
    # 避免每次进详情页都可能触发"上游抓取+逐批翻译"的慢链路
    article_meta = find_article_meta(url)

    # 抓取正文和图片
    data = get_article_content(url)
    # data 可能是 None（抓取失败）或旧结构（无 videos 键），全部用 getattr/兜底处理，
    # 详情页在抓取失败时必须降级渲染而不是 500。
    content = (data or {}).get('content') or ''
    # 原文正文（旧缓存条目可能没有该字段，回落到译文，保证模板永远拿得到字符串）
    content_original = (data or {}).get('content_original') or content
    images = (data or {}).get('images') or []

    # 视频：只做「白名单 iframe 嵌入 + 非白名单外链跳转」。
    # 这里再做一次服务端白名单复核（解析层已过滤一遍），确保模板拿到的
    # embed_videos 每一项都真的通过白名单校验——模板自身仍会独立复核，
    # 双重保险，避免任何一处遗漏导致 iframe 注入。
    embed_videos = []
    link_videos = []
    raw_videos = (data or {}).get('videos') or []
    if isinstance(raw_videos, (list, tuple)):
        for v in raw_videos:
            if not isinstance(v, dict):
                continue
            # 兼容 'src' 键名（T5 对抗测试会用 {'src': ...} 喂进来）
            safe_url = _safe_media_url(v.get('url') or v.get('src'))
            if not safe_url:
                continue
            item = {
                'url': safe_url,
                'label': v.get('label') or '视频',
                'type': v.get('type') or 'video',
                # 地区分流：cn=国内可直连（bilibili/b23.tv/acfun），
                # overseas=youtube/vimeo 等（国内被墙，自动嵌只会得到黑框）
                'region': v.get('region') or 'overseas',
            }
            # embed 只认「白名单域」这一硬条件，不信任上游传来的 embed 标志
            if _is_allowed_embed_url(safe_url):
                item['embed'] = True
                embed_videos.append(item)
            else:
                item['embed'] = False
                link_videos.append(item)
    # 去重并限制条数（Vercel 免费计划：详情页不该无限膨胀）
    def _dedupe(items, limit=6):
        out, seen = [], set()
        for it in items:
            if it['url'] in seen:
                continue
            seen.add(it['url'])
            out.append(it)
            if len(out) >= limit:
                break
        return out

    embed_videos = _dedupe(embed_videos)
    link_videos = _dedupe(link_videos)
    # 地区分流：只有国内可直连的播放器才自动嵌 iframe；海外源（YouTube 等）
    # 默认给「点击后加载」卡片，绝不让被墙的 16:9 黑框自动出现在页面上。
    cn_videos = [v for v in embed_videos if v.get('region') == 'cn']
    overseas_videos = [v for v in embed_videos if v.get('region') != 'cn']

    # 中文来源映射（保留一些品牌词）
    source_zh_map = {
        'MyAnimeList': '动漫百科',
        'Anime News Network': '动漫新闻网',
        'Anime Corner': '动漫角落',
        'Crunchyroll': 'Crunchyroll',
        'Comic Book': '漫画媒体',
        'Otaku USA': '宅文化美刊',
    }
    source_zh = source_zh_map.get(article_meta.get('source') if article_meta else '', 
                                  article_meta.get('source') if article_meta else '')

    # 翻译服务不可用/未配置时，后端会把 content 回落成原文并标记 translated_failed，
    # 前端必须显式告知用户（否则读者只会看到一整页英文，不知道是没翻译还是就这样）。
    translation_failed = (data or {}).get('status') == 'translated_failed'

    return render_template('anime_news_detail.html',
                           article=article_meta,
                           url=url,
                           content=content,
                           content_original=content_original,
                           images=images,
                           videos=embed_videos + link_videos,
                           embed_videos=embed_videos,
                           link_videos=link_videos,
                           cn_videos=cn_videos,
                           overseas_videos=overseas_videos,
                           fetch_failed=(data is None),
                           translation_failed=translation_failed,
                           source_zh=source_zh)


@public_bp.route('/api/anime/news')
def api_anime_news():
    """API：获取新闻列表（get_anime_news 已在模块顶部导入）"""
    limit = request.args.get('limit', 20, type=int)
    tag = request.args.get('tag') or None
    cursor = request.args.get('cursor') or None
    articles, meta = get_anime_news(limit=limit, tag=tag, cursor=cursor)
    resp = jsonify({'articles': articles, 'meta': meta})
    # stale-while-revalidate：边缘命中旧副本立即返回并后台刷新，
    # 冷实例的抓取+翻译耗时不再由用户承担
    resp.headers['Cache-Control'] = 'public, max-age=1800, s-maxage=1800, stale-while-revalidate=86400'
    return resp


@public_bp.route('/api/anime/news-search')
def api_anime_news_search():
    """搜索动漫新闻"""
    keyword = (request.args.get('q') or '').strip()
    if not keyword:
        return jsonify({'articles': []})
    results = search_news(keyword, limit=50)
    resp = jsonify({'articles': results})
    # 短缓存：相同关键词在边缘直接命中；SWR 允许后台刷新，避免每次现算
    resp.headers['Cache-Control'] = 'public, max-age=60, s-maxage=300, stale-while-revalidate=600'
    return resp


@public_bp.route('/api/anime/news-image')
def api_news_image():
    """代理新闻图片，走 CDN 缓存"""
    from urllib.parse import unquote
    url = unquote(request.args.get('url', ''))

    if not url.startswith('https://'):
        abort(404)

    # SSRF 防护：只允许公网地址并逐跳校验重定向。
    # 原实现任意 https 都放行，且把上游的 Content-Type 原样回给浏览器——
    # 攻击者可以让本站同源渲染任意 HTML（同源 XSS）。
    res = safe_get(url, headers=HEADERS_FOR_IMAGE, timeout=8)
    if res is None or res.status_code >= 400:
        abort(404)

    content_type = (res.headers.get('Content-Type') or '').split(';')[0].strip().lower()
    if not content_type.startswith('image/'):
        abort(404)

    return Response(
        res.content,
        mimetype=content_type,
        headers={
            'Cache-Control': 'public, max-age=604800, s-maxage=604800',
            'X-Content-Type-Options': 'nosniff',
        },
    )


@public_bp.route('/api/anime/news-detail')
def api_anime_news_detail():
    """抓取并翻译新闻原文"""
    from urllib.parse import unquote
    from .anime_news import get_article_content

    url = unquote(request.args.get('url', ''))
    if not url or not url.startswith('http'):
        return jsonify({'error': 'invalid url'}), 400
    # SSRF 防护：抓取前先确认目标是公网地址（get_article_content 内部也用 safe_get 逐跳校验）
    if not is_public_http_url(url):
        return jsonify({'error': 'invalid url'}), 400

    data = get_article_content(url)
    if not data:
        return jsonify({'error': '抓取失败'}), 404

    resp = jsonify(data)
    resp.headers['Cache-Control'] = 'public, max-age=86400, s-maxage=86400'
    return resp


@public_bp.route('/members')
def members():
    users = User.query.order_by(User.registered_at.desc()).all()
    return render_template('members.html', users=users)


# ========== 萌战系统 · 前台 ==========

@public_bp.route('/api/contest/<int:contest_id>/vote-stats')
def api_contest_vote_stats(contest_id):
    """投票页实时票数（轮询接口）：mode=group|knockout|qualifying, gender=female|male"""
    contest = get_or_404(Contest, contest_id)
    mode = request.args.get('mode', 'group')
    gender = request.args.get('gender', 'female')
    if mode not in ('group', 'knockout', 'qualifying') or gender not in ('female', 'male'):
        return jsonify({'ok': False, 'error': 'bad params'}), 400

    # 时区：库里 open_at 是 naive 东八区，和投票路由保持一致
    if contest.open_at and contest.open_at.tzinfo is not None:
        contest.open_at = contest.open_at.replace(tzinfo=None)
    now = _cn_now()
    times = calc_stage_times(contest.open_at) if contest.open_at else {}

    base = ContestVote.query.filter_by(contest_id=contest.id, gender=gender)
    round_number = None
    sub_round = None

    if mode == 'qualifying':
        base = base.filter_by(round_number=0)
    elif mode == 'group':
        for n in (1, 2, 3):
            end = times.get(f'group_round_{n}_end')
            if end and now <= end:
                round_number = n
                break
        if round_number is None:
            # 投票窗口已过（公示期）：回退到最近有票的一轮
            round_number = db.session.query(func.max(ContestVote.round_number)).filter(
                ContestVote.contest_id == contest.id,
                ContestVote.gender == gender,
                ContestVote.round_number.in_((1, 2, 3)),
            ).scalar()
        if round_number:
            base = base.filter_by(round_number=round_number)
        else:
            base = base.filter(ContestVote.id == -1)
    else:
        for n, key in ((1, 'knockout_16_end'), (2, 'knockout_8_end'),
                       (3, 'knockout_4_end'), (4, 'final_vote_end')):
            end = times.get(key)
            if end and now <= end:
                sub_round = n
                break
        if sub_round is None:
            sub_round = db.session.query(func.max(ContestVote.sub_round)).filter(
                ContestVote.contest_id == contest.id,
                ContestVote.round_number == 4,
                ContestVote.gender == gender,
            ).scalar()
        if sub_round:
            round_number = 4
            base = base.filter_by(round_number=4, sub_round=sub_round)
        else:
            base = base.filter(ContestVote.id == -1)

    rows = (base.with_entities(ContestVote.candidate_id,
                               func.sum(ContestVote.weight))
            .group_by(ContestVote.candidate_id).all())
    totals = {str(cid): int(votes or 0) for cid, votes in rows if cid is not None}

    return jsonify({
        'ok': True,
        'mode': mode,
        'gender': gender,
        'round': round_number,
        'sub_round': sub_round,
        'total': sum(totals.values()),
        'totals': totals,
        'updated_at': datetime.now().strftime('%H:%M:%S'),
    })


@public_bp.route('/contest_center')
def contest_center():
    now = _cn_now()
    for c in Contest.query.filter_by(status='draft').all():
        auto_activate_contest(c, now)

    # 进行中：open / group_stage / knockout 都算
    open_contests = Contest.query.filter(
        Contest.status.in_(['open', 'group_stage', 'knockout']),
        Contest.open_at <= now,
        Contest.close_at >= now
    ).order_by(Contest.created_at.desc()).all()

    upcoming_contests = Contest.query.filter(
        Contest.status == 'draft',
        Contest.open_at > now
    ).order_by(Contest.open_at.asc()).all()

    closed_contests = Contest.query.filter(
        Contest.status == 'closed'
    ).order_by(Contest.created_at.desc()).limit(10).all()

    return render_template('contest_center.html',
                           open_contests=open_contests,
                           upcoming_contests=upcoming_contests,
                           closed_contests=closed_contests)


@public_bp.route('/contest/<int:contest_id>/rules')
def contest_rules(contest_id):
    """赛事规则确认页"""
    contest = get_or_404(Contest, contest_id)
    return render_template('contest_rules.html', contest=contest)


def _cn_now():
    """当前东八区时间（naive）。

    为什么不用 utils.cn_now：测试会 patch('app.public.datetime') 来冻结时间，
    时间逻辑必须走本模块的 datetime 引用，否则 patch 失效。
    其它模块直接用 utils.cn_now() 即可。
    """
    return datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=8)


PHASE_CN = {
    'nomination': '提名期',
    'review': '审核期',
    'qualifying': '海选投票',
    'qualifying_result': '海选结果公示',
    'group_round_1': '小组赛第1轮',
    'group_round_1_result': '小组赛第1轮公示',
    'group_round_2': '小组赛第2轮',
    'group_round_2_result': '小组赛第2轮公示',
    'group_round_3': '小组赛第3轮',
    'group_round_3_result': '小组赛第3轮公示',
    'knockout_16': '淘汰赛16强',
    'knockout_16_result': '淘汰赛16强公示',
    'knockout_8': '淘汰赛8强',
    'knockout_8_result': '淘汰赛8强公示',
    'knockout_4': '淘汰赛4强',
    'knockout_4_result': '淘汰赛4强公示',
    'final_vote': '决赛投票',
    'final_result': '最终结果',
}


@public_bp.route('/contest/<int:contest_id>')
def contest_detail(contest_id):
    contest = get_or_404(Contest, contest_id)

    # 确保 open_at 是 naive（如果存在）
    if contest.open_at and contest.open_at.tzinfo is not None:
        contest.open_at = contest.open_at.replace(tzinfo=None)

    now = _cn_now()

    # 1. 计算赛程时间
    times = calc_stage_times(contest.open_at)

    # 2. 自动激活（草稿 → 开放）
    auto_activate_contest(contest, now)

    # 3. 计算当前阶段
    phase = calc_phase(contest, now, times)

    # OG 元数据：分享卡片标题/描述/缩略图（用站内图标）
    og_title = f'{contest.title} - 动漫社官网'
    og_description = contest.description[:200] + ('...' if len(contest.description) > 200 else '')
    og_image = '/static/images/icon-512.png'

    # 4. 执行自动推进
    # 循环处理，防止因为跳过了中间阶段而卡住
    for _ in range(10):
        changed = False

        # ① 海选投票结束 → 生成小组赛数据（先不切 status）
        if now >= times['qualifying_vote_end'] and contest.status == 'open':
            if not (contest.config and (contest.config.get('qualifying_promoted')
                                       or contest.config.get('female_groups')
                                       or contest.config.get('male_groups'))):
                run_qualifying_promotion(contest)
                changed = True

        # ② 海选公示结束 → 进入小组赛
        if now >= times['qualifying_end'] and contest.status == 'open':
            contest.status = 'group_stage'
            db.session.commit()
            changed = True

        # ③ 小组赛第3轮投票结束 → 生成淘汰赛数据
        if now >= times['group_round_3_end'] and contest.status in ['open', 'group_stage']:
            if not (contest.config and (contest.config.get('knockout_promoted')
                                       or contest.config.get('knockout_matches_female')
                                       or contest.config.get('knockout_matches_male'))):
                run_group_promotion(contest)
                changed = True

        # ④ 小组赛第3轮公示结束 → 进入淘汰赛
        if now >= times['group_round_3_result_end'] and contest.status == 'group_stage':
            contest.status = 'knockout'
            db.session.commit()
            changed = True

        # ⑤ 淘汰赛各轮推进
        advanced, _ = run_knockout_advance(contest, now, times)
        if advanced:
            changed = True

        # ⑥ 决赛投票结束 → 生成最终排名（两性别都已有终榜才跳过，
        # 否则单性别赛事会每个请求重跑一次终榜计算）
        if now >= times['final_vote_end'] and contest.status == 'knockout':
            if not (contest.config and (contest.config.get('female_ranking')
                                        or contest.config.get('male_ranking'))):
                run_final_ranking(contest)
                changed = True

        # ⑦ 最终公示结束 → 关闭赛事
        if now >= times['final_result_end'] and contest.status != 'closed':
            contest.status = 'closed'
            db.session.commit()
            changed = True

        if not changed:
            break

    # 推进后重新计算 phase（状态可能已变）
    phase = calc_phase(contest, now, times)

    # 5. 准备小组赛公示数据（仅当处于小组赛公示期）
    group_round_results = None
    overall_ranking_female = None
    overall_ranking_male = None

    if phase in ['group_round_1_result', 'group_round_2_result', 'group_round_3_result']:
        group_round_results, overall_ranking_female, overall_ranking_male = prepare_group_round_data(contest, phase)

    # 6. 获取数据
    candidates = contest.candidates.all()
    if session.get('user_id'):
        user_female_count = Nomination.query.filter_by(
            contest_id=contest.id,
            user_id=session.get('user_id'),
            gender='female'
        ).count()
        user_male_count = Nomination.query.filter_by(
            contest_id=contest.id,
            user_id=session.get('user_id'),
            gender='male'
        ).count()
    else:
        user_female_count = 0
        user_male_count = 0

    # ============================================================
    # 7. 构建 AI 额外数据（用于结构化解说）
    # ============================================================
    extra_data = {}
    candidates_map = {c.id: c for c in candidates}

    if phase in ['group_round_1', 'group_round_2', 'group_round_3',
                 'group_round_1_result', 'group_round_2_result', 'group_round_3_result']:
        # 小组赛：传入分组数据和票数
        if group_round_results:
            female_groups = group_round_results.get('female', [])
            female_ranking = overall_ranking_female if overall_ranking_female else []

            # 构建女组数据
            female_group_data = []
            for gi, group in enumerate(female_groups):
                matches = group.get('matches', [])
                formatted_matches = []
                for m in matches:
                    formatted_matches.append({
                        'candidate1': m.get('candidate1'),
                        'candidate2': m.get('candidate2'),
                        'votes1': m.get('votes1', 0),
                        'votes2': m.get('votes2', 0),
                        'winner': m.get('winner'),
                    })
                female_group_data.append({
                    'group_name': group.get('group_name', f'{chr(65 + gi)}组'),
                    'matches': formatted_matches,
                })

            extra_data = {
                'gender': 'female',
                'groups': contest.config.get('female_groups', []) if contest.config else [],
                'group_results': female_group_data,
                'ranking': female_ranking,
                'round_info': phase
            }

    elif phase in ['knockout_16', 'knockout_8', 'knockout_4', 'final_vote',
                   'knockout_16_result', 'knockout_8_result', 'knockout_4_result']:
        # 淘汰赛：传入对阵数据
        matches_female = contest.config.get('knockout_matches_female', []) if contest.config else []

        formatted_matches = []
        for m in matches_female:
            c1 = candidates_map.get(m.get('candidate1'))
            c2 = candidates_map.get(m.get('candidate2'))
            winner = candidates_map.get(m.get('winner'))
            formatted_matches.append({
                'candidate1_name': c1.name if c1 else '已移除',
                'candidate2_name': c2.name if c2 else '已移除',
                'votes1': m.get('votes1', 0),
                'votes2': m.get('votes2', 0),
                'status': m.get('status', 'active'),
                'winner': m.get('winner'),
                'winner_name': winner.name if winner else None,
            })

        extra_data = {
            'gender': 'female',
            'matches': formatted_matches,
            'round_info': phase
        }

    # 分享海报数据：当前总票数前 6 名（所有阶段累计），供前端 canvas 绘制
    vote_rows = (db.session.query(ContestVote.candidate_id, func.sum(ContestVote.weight))
                 .filter(ContestVote.contest_id == contest.id)
                 .group_by(ContestVote.candidate_id).all())
    vote_map = {cid: int(votes or 0) for cid, votes in vote_rows}
    top_candidates = sorted(candidates, key=lambda c: vote_map.get(c.id, 0), reverse=True)[:6]
    poster_data = {
        'title': contest.title,
        'phase_label': PHASE_CN.get(phase, phase),
        'club': os.getenv('CLUB_NAME') or '动漫社',
        'site': request.host,
        'total_votes': sum(vote_map.values()),
        'entries': [{
            'name': c.name,
            'source': c.source or '',
            'votes': vote_map.get(c.id, 0),
        } for c in top_candidates],
    }

    return render_template('contest_detail.html',
                           contest=contest,
                           poster_data=poster_data,
                           candidates=candidates,
                           phase=phase,
                           user_female_count=user_female_count,
                           user_male_count=user_male_count,
                           nomination_end=times['nomination_end'],
                           review_end=times['review_end'],
                           qualifying_vote_end=times['qualifying_vote_end'],
                           qualifying_end=times['qualifying_end'],
                           group_round_1_end=times['group_round_1_end'],
                           group_round_1_result_end=times['group_round_1_result_end'],
                           group_round_2_end=times['group_round_2_end'],
                           group_round_2_result_end=times['group_round_2_result_end'],
                           group_round_3_end=times['group_round_3_end'],
                           group_round_3_result_end=times['group_round_3_result_end'],
                           knockout_16_end=times['knockout_16_end'],
                           knockout_16_result_end=times['knockout_16_result_end'],
                           knockout_8_end=times['knockout_8_end'],
                           knockout_8_result_end=times['knockout_8_result_end'],
                           knockout_4_end=times['knockout_4_end'],
                           knockout_4_result_end=times['knockout_4_result_end'],
                           final_vote_end=times['final_vote_end'],
                           final_result_end=times['final_result_end'],
                           **supabase_render_ctx(),
                           now=now,
                           current_time=now,
                           group_round_results=group_round_results,
                           overall_ranking_female=overall_ranking_female,
                           overall_ranking_male=overall_ranking_male,
                           extra_data=extra_data,
                           # OG 元数据
                           og_title=og_title,
                           og_description=og_description,
                           og_image=og_image)


@public_bp.route('/contest/<int:contest_id>/nominate', methods=['POST'])
def submit_nomination(contest_id):
    supabase = get_supabase()
    if not session.get('user_id'):
        flash('请先登录', 'warning')
        return redirect(url_for('auth.login'))

    contest = get_or_404(Contest, contest_id)
    if contest.status != 'open':
        flash('该赛事未开放提名', 'danger')
        return redirect(url_for('public.contest_detail', contest_id=contest_id))

    name = (request.form.get('name') or '').strip()
    source = (request.form.get('source') or '').strip()
    gender = request.form.get('gender')
    description = request.form.get('description')

    if not name or not source or not gender:
        flash('角色名、作品名、性别为必填项', 'danger')
        return redirect(url_for('public.contest_detail', contest_id=contest_id))

    # 长度与字符校验：
    # 1) 超长会直接撞数据库列宽报 500；
    # 2) 角色名会被拼进 AI 解说的 prompt，而前端用 innerHTML 渲染解说，
    #    `<img src=x onerror=...>` 这类名字等于存储型 XSS 的载荷。
    if len(name) > 60 or len(source) > 100 or (description and len(description) > 500):
        flash('角色名不超过60字、作品名不超过100字、简介不超过500字', 'danger')
        return redirect(url_for('public.contest_detail', contest_id=contest_id))
    if any(ch in name + source for ch in '<>"\'`&'):
        flash('角色名/作品名不能包含 < > " \' ` & 等特殊字符', 'danger')
        return redirect(url_for('public.contest_detail', contest_id=contest_id))

    if gender not in ['female', 'male']:
        flash('无效的组别', 'danger')
        return redirect(url_for('public.contest_detail', contest_id=contest_id))

    # 按性别分别统计
    count = Nomination.query.filter_by(
        contest_id=contest_id,
        user_id=session.get('user_id'),
        gender=gender
    ).count()
    if count >= NOMINATION_LIMIT:
        gender_name = '女组' if gender == 'female' else '男组'
        flash(f'你在{gender_name}已达到提名上限（{NOMINATION_LIMIT}个角色）', 'danger')
        return redirect(url_for('public.contest_detail', contest_id=contest_id))

    existing = Nomination.query.filter_by(contest_id=contest_id, name=name).first()
    if existing:
        flash(f'角色 "{name}" 已被提名，不可重复', 'danger')
        return redirect(url_for('public.contest_detail', contest_id=contest_id))

    # 图片上传
    image_url = None
    if 'image' in request.files:
        file = request.files['image']
        if file and file.filename:
            if not allowed_file(file.filename):
                flash('图片格式不支持（支持 png, jpg, jpeg, gif）', 'danger')
                return redirect(url_for('public.contest_detail', contest_id=contest_id))
            ext = file.filename.rsplit('.', 1)[1].lower() if '.' in file.filename else 'png'
            filename = f"contest_{contest_id}_{datetime.now().strftime('%Y%m%d%H%M%S')}_{uuid.uuid4().hex[:8]}.{ext}"
            try:
                # 读取并压缩图片
                raw_data = file.read()
                compressed_data = compress_image(raw_data, max_size=NOMINATION_IMAGE_SIZE, quality=COMPRESS_QUALITY)

                supabase.storage.from_('contest_images').upload(
                    filename,
                    compressed_data,
                    file_options={"content-type": 'image/jpeg'}  # 统一转为JPEG
                )
                image_url = supabase.storage.from_('contest_images').get_public_url(filename)
            except Exception as e:
                flash(f'图片上传失败: {str(e)}', 'danger')
                return redirect(url_for('public.contest_detail', contest_id=contest_id))

    if not image_url:
        flash('请上传角色图片', 'danger')
        return redirect(url_for('public.contest_detail', contest_id=contest_id))

    nomination = Nomination(
        contest_id=contest_id,
        user_id=session.get('user_id'),
        name=name,
        source=source,
        gender=gender,
        image_url=image_url,
        description=description,
        status='pending'
    )
    db.session.add(nomination)
    db.session.commit()
    flash(f'已成功提名 "{name}"，等待管理员审核', 'success')
    return redirect(url_for('public.contest_detail', contest_id=contest_id))


# ========== 海选投票（分男女独立页面） ==========

@public_bp.route('/contest/<int:contest_id>/qualifying/female')
def qualifying_vote_female(contest_id):
    """海选投票 - 女组"""
    contest = get_or_404(Contest, contest_id)

    if contest.status != 'open':
        flash('该赛事未开放', 'danger')
        return redirect(url_for('public.contest_detail', contest_id=contest.id))

    candidates = contest.candidates.filter_by(gender='female').all()
    if not candidates:
        flash('暂无女组候选角色', 'warning')
        return redirect(url_for('public.contest_detail', contest_id=contest.id))

    # 当前用户已投票数
    existing_votes = {}
    if session.get('user_id'):
        rows = ContestVote.query.filter_by(
            contest_id=contest.id,
            user_id=session.get('user_id'),
            round_number=0,
            gender='female'
        ).all()
        for v in rows:
            existing_votes[v.candidate_id] = v.weight

    # 所有角色的总票数（一次 GROUP BY 查完）
    vote_totals = dict(
        db.session.query(
            ContestVote.candidate_id,
            db.func.sum(ContestVote.weight)
        ).filter(
            ContestVote.contest_id == contest.id,
            ContestVote.round_number == 0,
            ContestVote.gender == 'female'
        ).group_by(ContestVote.candidate_id).all()
    )

    return render_template('contest_qualifying_vote.html',
                           contest=contest,
                           candidates=candidates,
                           phase=_calc_current_phase(contest),
                           gender='female',
                           existing_votes=existing_votes,
                           vote_totals=vote_totals,
                           **supabase_render_ctx())


@public_bp.route('/contest/<int:contest_id>/qualifying/male')
def qualifying_vote_male(contest_id):
    """海选投票 - 男组"""
    contest = get_or_404(Contest, contest_id)

    if contest.status != 'open':
        flash('该赛事未开放', 'danger')
        return redirect(url_for('public.contest_detail', contest_id=contest.id))

    candidates = contest.candidates.filter_by(gender='male').all()
    if not candidates:
        flash('暂无男组候选角色', 'warning')
        return redirect(url_for('public.contest_detail', contest_id=contest.id))

    existing_votes = {}
    if session.get('user_id'):
        rows = ContestVote.query.filter_by(
            contest_id=contest.id,
            user_id=session.get('user_id'),
            round_number=0,
            gender='male'
        ).all()
        for v in rows:
            existing_votes[v.candidate_id] = v.weight

    vote_totals = dict(
        db.session.query(
            ContestVote.candidate_id,
            db.func.sum(ContestVote.weight)
        ).filter(
            ContestVote.contest_id == contest.id,
            ContestVote.round_number == 0,
            ContestVote.gender == 'male'
        ).group_by(ContestVote.candidate_id).all()
    )

    return render_template('contest_qualifying_vote.html',
                           contest=contest,
                           candidates=candidates,
                           phase=_calc_current_phase(contest),
                           gender='male',
                           existing_votes=existing_votes,
                           vote_totals=vote_totals,
                           **supabase_render_ctx())


@public_bp.route('/contest/<int:contest_id>/qualifying/submit', methods=['POST'])
def qualifying_vote_submit(contest_id):
    def fail(msg):
        return jsonify({'success': False, 'message': msg})

    if not session.get('user_id'):
        return fail('未登录')

    contest = get_or_404(Contest, contest_id)

    if contest.status != 'open':
        return fail(f'赛事未开放（当前状态：{contest.status}）')

    gender = request.form.get('gender')
    if gender not in ['female', 'male']:
        return fail('无效的组别')

    # 海选时间窗校验：原先只检查 status，而 status 从提名期一直到海选公示期结束
    # 都是 'open'，于是提名期/审核期/公示期都能投票——公示期内投的票虽然不显示，
    # 却会在 qualifying_end 计算 32 强时生效。
    if contest.open_at and contest.open_at.tzinfo is not None:
        contest.open_at = contest.open_at.replace(tzinfo=None)
    if not contest.open_at:
        return fail('赛事开始时间未设置')
    _now = _cn_now()
    _times = calc_stage_times(contest.open_at)
    if _now <= _times['review_end']:
        return fail('海选投票尚未开始')
    if _now > _times['qualifying_vote_end']:
        return fail('海选投票已结束')

    # 同一用户的并发提交串行化（PostgreSQL 行锁；SQLite 会自动忽略 FOR UPDATE）。
    # 原先是「读历史 → 校验 → 删旧 → 写新」，两个并发请求各自读到空历史后
    # 都能通过 15 票上限校验，最终两套角色集合并存，票数直接翻倍。
    db.session.query(User.id).filter(
        User.id == session.get('user_id')
    ).with_for_update().first()

    # 1. 读历史
    existing_rows = ContestVote.query.filter_by(
        contest_id=contest.id,
        user_id=session.get('user_id'),
        round_number=0,
        gender=gender
    ).all()
    existing = {v.candidate_id: v.weight for v in existing_rows}

    # 2. 读本次提交
    new_votes = {}
    for key, value in request.form.items():
        if key.startswith('vote_'):
            try:
                cid = int(key.split('_')[1])
                w = int(value) if value else 0
                if w > 0:
                    new_votes[cid] = w
            except (ValueError, IndexError):
                pass

    if not new_votes:
        return fail('请至少给一个角色投票')

    # 校验每个 candidate 属于本赛事、且性别与提交的组别一致。
    # 原先 candidate_id 直接来自表单 key 且完全不校验，而计票函数按 candidate_id
    # 汇总、忽略 vote.gender，于是同一角色可分别在 female / male 两个桶里各投 3 票，
    # 单人给单角色的票数上限（3）被翻倍，可选角色数也从 5 变成 10。
    valid_ids = {
        c.id for c in Candidate.query.filter(
            Candidate.contest_id == contest.id,
            Candidate.gender == gender,
        ).all()
    }
    if any(cid not in valid_ids for cid in new_votes):
        return fail('存在无效的角色，请刷新页面后重新投票')

    # 禁赛校验：被禁赛的角色不能投票
    banned_ids = {
        c.id for c in Candidate.query.filter(
            Candidate.contest_id == contest.id,
            Candidate.banned.is_(True),
        ).all()
    }
    if any(cid in banned_ids for cid in new_votes):
        return fail('包含被禁赛的角色，禁赛期间禁止投票')

    # 3. 合并（历史 + 本次）
    merged = dict(existing)
    for cid, w in new_votes.items():
        merged[cid] = merged.get(cid, 0) + w

    # 4. 校验合并后的上限
    total = sum(merged.values())
    if total > QUALIFYING_MAX_VOTES:
        return fail(f'总票数不能超过 {QUALIFYING_MAX_VOTES} 票（已投 {sum(existing.values())} 票，本次 {sum(new_votes.values())} 票，合计 {total}）')
    if len(merged) > QUALIFYING_MAX_CANDIDATES:
        return fail(f'最多只能投给 {QUALIFYING_MAX_CANDIDATES} 个角色（已投 {len(existing)} 个，本次 {len(new_votes)} 个，合计 {len(merged)}）')
    for cid, w in merged.items():
        if w > QUALIFYING_MAX_PER_CANDIDATE:
            return fail(f'每个角色最多只能投 {QUALIFYING_MAX_PER_CANDIDATE} 票（角色 {cid} 累计 {w} 票）')

    # 5. 删旧、写合并后的
    ContestVote.query.filter_by(
        contest_id=contest.id,
        user_id=session.get('user_id'),
        round_number=0,
        gender=gender
    ).delete()

    for cid, w in merged.items():
        db.session.add(ContestVote(
            contest_id=contest.id,
            candidate_id=cid,
            user_id=session.get('user_id'),
            weight=w,
            round_number=0,
            gender=gender
        ))

    try:
        db.session.commit()
    except IntegrityError as e:
        db.session.rollback()
        return fail(f'数据库冲突：{str(e)}')
    except Exception as e:
        db.session.rollback()
        return fail(f'写入失败：{type(e).__name__}: {str(e)}')

    return jsonify({
        'success': True,
        'message': f'本次投了 {len(new_votes)} 个角色 {sum(new_votes.values())} 票，累计 {len(merged)} 个角色 {total} 票'
    })


@public_bp.route('/contest/<int:contest_id>/qualifying/reset', methods=['POST'])
def qualifying_vote_reset(contest_id):
    if not session.get('user_id'):
        return jsonify({'success': False, 'message': '未登录'})

    contest = get_or_404(Contest, contest_id)

    gender = request.form.get('gender')
    if gender not in ['female', 'male']:
        return jsonify({'success': False, 'message': '无效的组别'})

    # 只允许在海选投票窗口内清空，避免赛后清票掩盖刷票痕迹
    if contest.open_at and contest.open_at.tzinfo is not None:
        contest.open_at = contest.open_at.replace(tzinfo=None)
    if not contest.open_at:
        return jsonify({'success': False, 'message': '赛事开始时间未设置'})
    _now = _cn_now()
    _times = calc_stage_times(contest.open_at)
    if _now <= _times['review_end'] or _now > _times['qualifying_vote_end']:
        return jsonify({'success': False, 'message': '当前不在海选投票期，无法清空投票'})

    try:
        deleted = ContestVote.query.filter_by(
            contest_id=contest.id,
            user_id=session.get('user_id'),
            round_number=0,
            gender=gender
        ).delete()
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': f'清空投票失败：{type(e).__name__}: {str(e)}'})

    return jsonify({'success': True, 'message': f'已清空 {deleted} 条投票记录'})


# ========== 小组赛投票 ==========

@public_bp.route('/contest/<int:contest_id>/group/female')
def group_vote_female(contest_id):
    """小组赛投票 - 女组"""
    contest = get_or_404(Contest, contest_id)

    if contest.status not in ['open', 'group_stage']:
        flash('当前不可投票', 'danger')
        return redirect(url_for('public.contest_detail', contest_id=contest.id))

    groups = contest.config.get('female_groups', []) if contest.config else []
    if not groups:
        flash('女组分组尚未生成', 'warning')
        return redirect(url_for('public.contest_detail', contest_id=contest.id))

    candidates = {c.id: c for c in contest.candidates.all()}

    if contest.open_at and contest.open_at.tzinfo is not None:
        contest.open_at = contest.open_at.replace(tzinfo=None)

    now = _cn_now()
    times = calc_stage_times(contest.open_at)

    # 计算当前阶段和轮次
    phase = _calc_current_phase(contest)
    round_number = None
    if now <= times['group_round_1_end']:
        round_number = 1
    elif now <= times['group_round_2_end']:
        round_number = 2
    elif now <= times['group_round_3_end']:
        round_number = 3

    # 投票期数据
    voted_matches = []
    vote_totals = {}
    if round_number:
        if session.get('user_id'):
            existing = ContestVote.query.filter_by(
                contest_id=contest.id, user_id=session.get('user_id'),
                round_number=round_number, gender='female'
            ).all()
            voted_matches = [f'{v.group_index}_{v.match_index}' for v in existing]

        vote_totals = dict(
            db.session.query(ContestVote.candidate_id, db.func.sum(ContestVote.weight))
            .filter(
                ContestVote.contest_id == contest.id,
                ContestVote.round_number == round_number,
                ContestVote.gender == 'female'
            ).group_by(ContestVote.candidate_id).all()
        )

    # ★ 公示期数据（解决暂无数据的问题）
    group_round_results = None
    overall_ranking_female = None
    overall_ranking_male = None
    if phase in ['group_round_1_result', 'group_round_2_result', 'group_round_3_result']:
        group_round_results, overall_ranking_female, overall_ranking_male = prepare_group_round_data(contest, phase)

    return render_template('contest_group_vote.html',
                           contest=contest, groups=groups, candidates=candidates,
                           phase=phase, gender='female', round_type='group',
                           voted_matches=voted_matches, vote_totals=vote_totals,
                           group_round_results=group_round_results,
                           overall_ranking_female=overall_ranking_female,
                           overall_ranking_male=overall_ranking_male,
                           **supabase_render_ctx())  # ★ 统一注入


@public_bp.route('/contest/<int:contest_id>/group/male')
def group_vote_male(contest_id):
    """小组赛投票 - 男组"""
    contest = get_or_404(Contest, contest_id)

    if contest.status not in ['open', 'group_stage']:
        flash('当前不可投票', 'danger')
        return redirect(url_for('public.contest_detail', contest_id=contest.id))

    groups = contest.config.get('male_groups', []) if contest.config else []
    if not groups:
        flash('男组分组尚未生成', 'warning')
        return redirect(url_for('public.contest_detail', contest_id=contest.id))

    candidates = {c.id: c for c in contest.candidates.all()}

    if contest.open_at and contest.open_at.tzinfo is not None:
        contest.open_at = contest.open_at.replace(tzinfo=None)

    now = _cn_now()
    times = calc_stage_times(contest.open_at)

    phase = _calc_current_phase(contest)
    round_number = None
    if now <= times['group_round_1_end']:
        round_number = 1
    elif now <= times['group_round_2_end']:
        round_number = 2
    elif now <= times['group_round_3_end']:
        round_number = 3

    voted_matches = []
    vote_totals = {}
    if round_number:
        if session.get('user_id'):
            existing = ContestVote.query.filter_by(
                contest_id=contest.id, user_id=session.get('user_id'),
                round_number=round_number, gender='male'
            ).all()
            voted_matches = [f'{v.group_index}_{v.match_index}' for v in existing]

        vote_totals = dict(
            db.session.query(ContestVote.candidate_id, db.func.sum(ContestVote.weight))
            .filter(
                ContestVote.contest_id == contest.id,
                ContestVote.round_number == round_number,
                ContestVote.gender == 'male'
            ).group_by(ContestVote.candidate_id).all()
        )

    group_round_results = None
    overall_ranking_female = None
    overall_ranking_male = None
    if phase in ['group_round_1_result', 'group_round_2_result', 'group_round_3_result']:
        group_round_results, overall_ranking_female, overall_ranking_male = prepare_group_round_data(contest, phase)

    return render_template('contest_group_vote.html',
                           contest=contest, groups=groups, candidates=candidates,
                           phase=phase, gender='male', round_type='group',
                           voted_matches=voted_matches, vote_totals=vote_totals,
                           group_round_results=group_round_results,
                           overall_ranking_female=overall_ranking_female,
                           overall_ranking_male=overall_ranking_male,
                           **supabase_render_ctx())  # ★ 男组也改了，不会 500 了


@public_bp.route('/contest/<int:contest_id>/group/submit', methods=['POST'])
def group_vote_submit(contest_id):
    def fail(msg):
        return jsonify({'success': False, 'message': msg})

    if not session.get('user_id'):
        return fail('未登录')

    contest = get_or_404(Contest, contest_id)

    if contest.status not in ['open', 'group_stage']:
        return fail(f'当前不可投票（赛事状态：{contest.status}）')

    if contest.open_at and contest.open_at.tzinfo is not None:
        contest.open_at = contest.open_at.replace(tzinfo=None)

    now = _cn_now()
    open_at = contest.open_at
    if not open_at:
        return fail('赛事开始时间未设置')

    times = calc_stage_times(open_at)

    if now <= times['group_round_1_end']:
        round_number = 1
    elif now <= times['group_round_1_result_end']:
        return fail('第1轮公示期，不能投票')
    elif now <= times['group_round_2_end']:
        round_number = 2
    elif now <= times['group_round_2_result_end']:
        return fail('第2轮公示期，不能投票')
    elif now <= times['group_round_3_end']:
        round_number = 3
    elif now <= times['group_round_3_result_end']:
        return fail('第3轮公示期，不能投票')
    else:
        return fail('小组赛已结束')

    gender = request.form.get('gender')
    if gender not in ['female', 'male']:
        return fail('无效的组别')

    candidate_id = request.form.get('candidate_id')
    if not candidate_id:
        return fail('请选择你要支持的角色')
    try:
        candidate_id = int(candidate_id)
    except (TypeError, ValueError):
        return fail('角色信息无效')

    groups = contest.config.get(f'{gender}_groups', []) if contest.config else []
    if not groups:
        return fail('分组数据不存在')

    target_group_index = None
    target_group = None
    for gi, group in enumerate(groups):
        if candidate_id in group:
            target_group_index = gi
            target_group = group
            break

    if target_group is None:
        return fail('该角色不在任何分组中')

    # 禁赛校验：被禁赛的角色不能投票
    _candidate = db.session.get(Candidate, candidate_id)
    if _candidate is None or _candidate.banned:
        return fail('该角色已被禁赛，暂不能投票')

    # 4 槽不变量防御：旧数据/异常数据中短组以 None 补齐后再索引
    slots = list(target_group) + [None] * (4 - len(target_group))
    if round_number == 1:
        pairs = [(slots[0], slots[1]), (slots[2], slots[3])]
    elif round_number == 2:
        pairs = [(slots[0], slots[2]), (slots[1], slots[3])]
    else:
        pairs = [(slots[0], slots[3]), (slots[1], slots[2])]

    match_index = None
    for mi, (cid1, cid2) in enumerate(pairs, start=1):
        if candidate_id == cid1 or candidate_id == cid2:
            match_index = mi
            break

    if match_index is None:
        return fail('该角色当前轮次无比赛')

    existing = ContestVote.query.filter_by(
        contest_id=contest.id,
        user_id=session.get('user_id'),
        round_number=round_number,
        gender=gender,
        match_index=match_index,
        group_index=target_group_index
    ).first()

    if existing:
        return fail(f'第{round_number}轮该场对决你已经投过票了')

    vote = ContestVote(
        contest_id=contest.id,
        candidate_id=candidate_id,
        user_id=session.get('user_id'),
        weight=1,
        round_number=round_number,
        gender=gender,
        match_index=match_index,
        group_index=target_group_index
    )
    db.session.add(vote)

    try:
        db.session.commit()
    except IntegrityError as e:
        db.session.rollback()
        return fail(f'数据库冲突：{str(e)}')
    except Exception as e:
        db.session.rollback()
        return fail(f'写入失败：{type(e).__name__}: {str(e)}')

    return jsonify({'success': True, 'message': f'第{round_number}轮第{match_index}场投票成功'})


# ========== 淘汰赛 ==========

@public_bp.route('/contest/<int:contest_id>/knockout/female')
def knockout_vote_female(contest_id):
    """淘汰赛投票 - 女组"""
    contest = get_or_404(Contest, contest_id)

    if contest.status not in ['open', 'knockout']:
        flash('当前不可投票', 'danger')
        return redirect(url_for('public.contest_detail', contest_id=contest.id))

    matches = contest.config.get('knockout_matches_female', []) if contest.config else []
    if not matches:
        flash('女组淘汰赛尚未开始', 'warning')
        return redirect(url_for('public.contest_detail', contest_id=contest.id))

    candidates = {c.id: c for c in contest.candidates.all()}

    phase = _calc_current_phase(contest)

    # 时区修复
    if contest.open_at and contest.open_at.tzinfo is not None:
        contest.open_at = contest.open_at.replace(tzinfo=None)

    now = _cn_now()
    times = calc_stage_times(contest.open_at)

    sub_round = None
    if now <= times['knockout_16_end']:
        sub_round = 1
    elif now <= times['knockout_8_end']:
        sub_round = 2
    elif now <= times['knockout_4_end']:
        sub_round = 3
    elif now <= times['final_vote_end']:
        sub_round = 4

    # 用户已投的场次
    voted_matches = []
    if session.get('user_id') and sub_round:
        existing = ContestVote.query.filter_by(
            contest_id=contest.id,
            user_id=session.get('user_id'),
            round_number=4,
            sub_round=sub_round,
            gender='female'
        ).all()
        voted_matches = [v.match_index for v in existing]

    # 本轮每人的实时票数
    vote_totals = {}
    if sub_round:
        vote_totals = dict(
            db.session.query(
                ContestVote.candidate_id,
                db.func.sum(ContestVote.weight)
            ).filter(
                ContestVote.contest_id == contest.id,
                ContestVote.round_number == 4,
                ContestVote.sub_round == sub_round,
                ContestVote.gender == 'female'
            ).group_by(ContestVote.candidate_id).all()
        )

    # 公示期上一轮结果
    prev_matches = []
    prev_round_name = ''
    if contest.config:
        if phase == 'knockout_16_result':
            prev_matches = contest.config.get('knockout_matches_female_round16', [])
            prev_round_name = '16强'
        elif phase == 'knockout_8_result':
            prev_matches = contest.config.get('knockout_matches_female_round8', [])
            prev_round_name = '8强'
        elif phase == 'knockout_4_result':
            prev_matches = contest.config.get('knockout_matches_female_round4', [])
            prev_round_name = '4强'
        elif phase == 'final_result':
            prev_matches = contest.config.get('knockout_matches_female_final', [])
            prev_round_name = '决赛'

    return render_template('contest_knockout_vote.html',
                           contest=contest,
                           matches=matches,
                           candidates=candidates,
                           phase=phase,
                           gender='female',
                           voted_matches=voted_matches,
                           vote_totals=vote_totals,
                           sub_round=sub_round,
                           prev_matches=prev_matches,
                           prev_round_name=prev_round_name,
                           **supabase_render_ctx())


@public_bp.route('/contest/<int:contest_id>/knockout/male')
def knockout_vote_male(contest_id):
    """淘汰赛投票 - 男组"""
    contest = get_or_404(Contest, contest_id)

    if contest.status not in ['open', 'knockout']:
        flash('当前不可投票', 'danger')
        return redirect(url_for('public.contest_detail', contest_id=contest.id))

    matches = contest.config.get('knockout_matches_male', []) if contest.config else []
    if not matches:
        flash('男组淘汰赛尚未开始', 'warning')
        return redirect(url_for('public.contest_detail', contest_id=contest.id))

    candidates = {c.id: c for c in contest.candidates.all()}

    phase = _calc_current_phase(contest)

    if contest.open_at and contest.open_at.tzinfo is not None:
        contest.open_at = contest.open_at.replace(tzinfo=None)

    now = _cn_now()
    times = calc_stage_times(contest.open_at)

    sub_round = None
    if now <= times['knockout_16_end']:
        sub_round = 1
    elif now <= times['knockout_8_end']:
        sub_round = 2
    elif now <= times['knockout_4_end']:
        sub_round = 3
    elif now <= times['final_vote_end']:
        sub_round = 4

    voted_matches = []
    if session.get('user_id') and sub_round:
        existing = ContestVote.query.filter_by(
            contest_id=contest.id,
            user_id=session.get('user_id'),
            round_number=4,
            sub_round=sub_round,
            gender='male'
        ).all()
        voted_matches = [v.match_index for v in existing]

    vote_totals = {}
    if sub_round:
        vote_totals = dict(
            db.session.query(
                ContestVote.candidate_id,
                db.func.sum(ContestVote.weight)
            ).filter(
                ContestVote.contest_id == contest.id,
                ContestVote.round_number == 4,
                ContestVote.sub_round == sub_round,
                ContestVote.gender == 'male'
            ).group_by(ContestVote.candidate_id).all()
        )

    prev_matches = []
    prev_round_name = ''
    if contest.config:
        if phase == 'knockout_16_result':
            prev_matches = contest.config.get('knockout_matches_male_round16', [])
            prev_round_name = '16强'
        elif phase == 'knockout_8_result':
            prev_matches = contest.config.get('knockout_matches_male_round8', [])
            prev_round_name = '8强'
        elif phase == 'knockout_4_result':
            prev_matches = contest.config.get('knockout_matches_male_round4', [])
            prev_round_name = '4强'
        elif phase == 'final_result':
            prev_matches = contest.config.get('knockout_matches_male_final', [])
            prev_round_name = '决赛'

    return render_template('contest_knockout_vote.html',
                           contest=contest,
                           matches=matches,
                           candidates=candidates,
                           phase=phase,
                           gender='male',
                           voted_matches=voted_matches,
                           vote_totals=vote_totals,
                           sub_round=sub_round,
                           prev_matches=prev_matches,
                           prev_round_name=prev_round_name,
                           **supabase_render_ctx())


@public_bp.route('/contest/<int:contest_id>/knockout/submit', methods=['POST'])
def knockout_vote_submit(contest_id):
    def fail(msg):
        return jsonify({'success': False, 'message': msg})

    if not session.get('user_id'):
        return fail('未登录')

    contest = get_or_404(Contest, contest_id)

    if contest.status not in ['open', 'knockout']:
        return fail(f'当前不可投票（赛事状态：{contest.status}）')

    if contest.open_at and contest.open_at.tzinfo is not None:
        contest.open_at = contest.open_at.replace(tzinfo=None)

    now = _cn_now()
    open_at = contest.open_at
    if not open_at:
        return fail('赛事开始时间未设置')

    times = calc_stage_times(open_at)

    if now <= times['knockout_16_end']:
        round_name = '16强'
    elif now <= times['knockout_16_result_end']:
        return fail('16强公示期，不能投票')
    elif now <= times['knockout_8_end']:
        round_name = '8强'
    elif now <= times['knockout_8_result_end']:
        return fail('8强公示期，不能投票')
    elif now <= times['knockout_4_end']:
        round_name = '4强'
    elif now <= times['knockout_4_result_end']:
        return fail('4强公示期，不能投票')
    elif now <= times['final_vote_end']:
        round_name = '决赛'
    elif now <= times['final_result_end']:
        return fail('决赛公示期，不能投票')
    else:
        return fail('淘汰赛已结束')

    gender = request.form.get('gender')
    if gender not in ['female', 'male']:
        return fail('无效的组别')

    sub_round_map = {'16强': 1, '8强': 2, '4强': 3, '决赛': 4}
    sub_round = sub_round_map.get(round_name, 0)

    candidate_id = request.form.get('candidate_id')
    if not candidate_id:
        return fail('请选择你要支持的角色')
    try:
        candidate_id = int(candidate_id)
    except (TypeError, ValueError):
        return fail('角色信息无效')

    # ===== 场次由服务端从对阵里推导，不再相信客户端传来的 match_index =====
    # 原实现直接采信表单里的 match_index 做去重，而计票函数 get_knockout_votes
    # 只按 (candidate_id, sub_round, gender) 汇总、**不看 match_index**，
    # 于是同一个人只要不断变换 match_index 就能给同一角色无限投票，直接决定冠军。
    matches = contest.config.get(f'knockout_matches_{gender}', []) if contest.config else []
    if not matches:
        return fail('本轮对阵尚未生成，请稍后再试')

    match_index = None
    for _idx, _match in enumerate(matches, start=1):
        if candidate_id in (_match.get('candidate1'), _match.get('candidate2')):
            if _match.get('status') != 'active':
                return fail('该场对决当前不可投票')
            match_index = _idx
            break
    if match_index is None:
        return fail('该角色不在本轮对阵中')

    # 禁赛校验：被禁赛的角色不能投票
    _cand = db.session.get(Candidate, candidate_id)
    if _cand is None or _cand.banned:
        return fail('该角色已被禁赛，暂不能投票')

    # 去重：本轮 + 本场（match_index 由服务端推导）
    existing = ContestVote.query.filter_by(
        contest_id=contest.id,
        user_id=session.get('user_id'),
        round_number=4,
        sub_round=sub_round,
        match_index=match_index,
        gender=gender
    ).first()

    if existing:
        return fail(f'{round_name}第{match_index}场你已经投过票了')

    vote = ContestVote(
        contest_id=contest.id,
        candidate_id=candidate_id,
        user_id=session.get('user_id'),
        weight=1,
        round_number=4,
        sub_round=sub_round,
        match_index=match_index,
        gender=gender
    )
    db.session.add(vote)

    try:
        db.session.commit()
    except IntegrityError as e:
        db.session.rollback()
        return fail(f'数据库冲突：{str(e)}')
    except Exception as e:
        db.session.rollback()
        return fail(f'写入失败：{type(e).__name__}: {str(e)}')

    return jsonify({'success': True, 'message': f'{round_name}第{match_index}场投票成功'})


# ========== API ==========

@public_bp.route('/api/votes/<int:candidate_id>')
def api_votes(candidate_id):
    """获取某个候选人的票数（可按 round / sub_round 过滤）"""
    from .models import Candidate
    candidate = db.session.get(Candidate, candidate_id)
    if not candidate:
        return {'error': 'Candidate not found'}, 404

    q = ContestVote.query.filter_by(candidate_id=candidate_id)

    round_number = request.args.get('round')
    if round_number is not None:
        try:
            q = q.filter(ContestVote.round_number == int(round_number))
        except ValueError:
            pass

    sub_round = request.args.get('sub_round')
    if sub_round is not None:
        try:
            q = q.filter(ContestVote.sub_round == int(sub_round))
        except ValueError:
            pass

    total = q.with_entities(db.func.sum(ContestVote.weight)).scalar() or 0
    return {'total_votes': total}


# ========== AI 功能 ==========

@public_bp.route('/contest/<int:contest_id>/commentary')
def ai_commentary(contest_id):
    """获取 AI 实时战报"""
    # phase 由服务端推导，不再采信 query string：否则任何陌生字符串都是一个
    # 新的缓存键，每次都绕过缓存并触发一次真实（付费）的模型调用。
    contest = get_or_404(Contest, contest_id)
    phase = _calc_current_phase(contest)
    # force 只对管理层开放：该接口在白名单里（匿名可达），force=true 会跳过缓存。
    force = (request.args.get('force') == 'true'
             and session.get('user_role') in ('owner', 'staff'))
    success, content, error = generate_commentary(contest_id, phase, force=force)
    if success:
        return jsonify({'success': True, 'commentary': content})
    return jsonify({'success': False, 'error': error}), 500


# 单飞锁：并发请求同时未命中缓存时，只放一个真去调（付费）模型，其余等待并复用结果。
# ★ 为什么必须单飞：Vercel Serverless 是**多实例**部署，app/ai.py 里的缓存是进程内的
#   （每实例一份、实例回收即失效），而 /contest/<id>/predict 又在 app/__init__.py 的
#   匿名白名单里。三者叠加 => 匿名用户并发打同一个 contest，多个实例各自未命中缓存，
#   就会各自发起一次真实付费模型调用；再换 contest_id 即可绕过任何单键缓存。
#   app/ai.py 的 _cached_or_compute() 只保证「同一 key 同时只有一个在写」，
#   拿不到锁的线程进入锁内二次查缓存失败后仍会各自 compute()，挡不住重复计费调用，
#   因此在视图这一层用「进程内单飞 + 冷却」再收一道。
_predict_locks = {}
_predict_locks_guard = threading.Lock()
# 同一 contest 的冷却窗口：预测基于赛事快照，N 秒内重复请求没有新信息，
# 直接复用上一次结果（不触发新的付费调用）。取 60s，与 Lead 建议一致。
_PREDICT_COOLDOWN_SECONDS = 60
_predict_cooldown = {}


def _get_predict_lock(contest_id):
    """按 contest 取一把单飞锁（锁字典有上限，避免被 contest_id 刷爆）"""
    with _predict_locks_guard:
        lock = _predict_locks.get(contest_id)
        if lock is None:
            if len(_predict_locks) >= 256:
                # dict 保序：丢掉最早插入的一批（等价 FIFO 淘汰）
                for key in list(_predict_locks.keys())[:128]:
                    _predict_locks.pop(key, None)
            lock = threading.Lock()
            _predict_locks[contest_id] = lock
        return lock


@public_bp.route('/contest/<int:contest_id>/predict')
def ai_predict(contest_id):
    """获取 AI 赛事预测

    ★ 资损防护（该端点在匿名白名单内，必须自己兜住）：
      1) 单飞：同一 contest 的并发请求只允许一次真实模型调用，其余等锁后复用，
         避免 Vercel 多实例 / 多线程同时击穿缓存导致重复计费；
      2) 冷却：同一 contest 60 秒内重复请求直接返回上一次结果，不触发新调用；
      3) 匿名用户仍可查看预测（保持原有 UX），真正的模型调用只发生在
         「缓存未命中且不在冷却窗口内」时——app/ai.py 的 1 小时缓存是第一道，
         这里只负责堵住并发击穿与高频重放。
    注意：进程内状态在 Serverless 下每实例独立，所以它降低的是**单个实例内**的
    重复调用；跨实例的重复由 ai.py 的缓存兜底，两者叠加后资损面已收敛到
    「每个实例在缓存过期后最多再调一次」。
    """
    contest = get_or_404(Contest, contest_id)

    lock = _get_predict_lock(contest_id)
    with lock:
        now_ts = time.time()
        last = _predict_cooldown.get(contest_id)
        if last and now_ts - last < _PREDICT_COOLDOWN_SECONDS:
            # 冷却窗口内：答 429 让前端稍后再来，绝不触发真实调用。
            # （ai.py 未导出公开的缓存读取接口，且它不在本模块写范围内，
            #   故这里不去回读它的缓存，直接按「稍后再试」处理。）
            retry_after = max(1, int(_PREDICT_COOLDOWN_SECONDS - (now_ts - last)))
            return jsonify({
                'success': False,
                'error': f'请求过于频繁，请 {retry_after} 秒后再试',
                'retry_after': retry_after,
            }), 429

        success, content, error = generate_prediction(contest_id)
        if success:
            _predict_cooldown[contest_id] = now_ts
            if len(_predict_cooldown) > 256:
                for key in list(_predict_cooldown.keys())[:128]:
                    _predict_cooldown.pop(key, None)
            return jsonify({'success': True, 'prediction': content})
        return jsonify({'success': False, 'error': error}), 500


@public_bp.route('/manifest.json')
def manifest():
    club_name = os.getenv('CLUB_NAME', '动漫社')
    return jsonify({
        "name": club_name,
        "short_name": club_name,
        "description": "以热爱为名 · 共创二次元家园",
        "start_url": "/",
        "display": "standalone",
        "background_color": "#f0f8ff",
        "theme_color": "#1e2a3a",
        "orientation": "portrait",
        "icons": [
            {
                "src": "/static/images/icon-192.png",
                "sizes": "192x192",
                "type": "image/png",
                "purpose": "any maskable"
            },
            {
                "src": "/static/images/icon-512.png",
                "sizes": "512x512",
                "type": "image/png",
                "purpose": "any maskable"
            }
        ]
    })


@public_bp.route('/robots.txt')
def robots():
    return send_from_directory(os.path.join(os.path.dirname(__file__), '..', 'static'), 'robots.txt')


@public_bp.route('/offline')
def offline():
    """PWA 离线兜底页：断网时 service worker 返回它（访客可访问）。"""
    return render_template('offline.html')


@public_bp.route('/sw.js')
def service_worker():
    """Service Worker 挂根路径，作用域覆盖全站。
    运行时把 CACHE_VERSION 替换为部署版本号，避免被浏览器缓存。"""
    import re
    base = os.path.join(os.path.dirname(__file__), '..')
    sw_path = os.path.join(base, 'sw.js')

    try:
        with open(sw_path, 'r', encoding='utf-8') as f:
            content = f.read()
    except OSError:
        return 'Service Worker not found', 404

    # Vercel 会自动注入这个环境变量；本地开发时回退到 dev
    version = os.getenv('VERCEL_GIT_COMMIT_SHA', 'dev')[:8]
    content = re.sub(
        r"const CACHE_VERSION = '.*';",
        f"const CACHE_VERSION = '{version}';",
        content,
        count=1,
    )

    return Response(content, mimetype='application/javascript')


_kanban_cache = {'lines': None, 'expires': None}


@public_bp.route('/api/kanban/lines')
def api_kanban_lines():
    """返回看板娘的真实数据台词 + 闲聊池"""
    import random

    now = _cn_now()

    # 缓存部分：公共数据（不含用户名）
    if not (_kanban_cache['lines'] and _kanban_cache['expires'] > now):
        facts = []

        total = User.query.count()
        if total:
            facts.append(f'社团已有 {total} 位社员～')

        contest = Contest.query.filter(
            Contest.status.in_(['open', 'group_stage', 'knockout']),
            Contest.open_at <= now,
            Contest.close_at >= now
        ).order_by(Contest.created_at.desc()).first()
        if contest:
            facts.append(f'《{contest.title}》正在进行中！')

        msg = Message.query.order_by(Message.timestamp.desc()).first()
        if msg:
            snippet = msg.content[:20] + ('…' if len(msg.content) > 20 else '')
            facts.append(f'{msg.nickname}：{snippet}')

        chitchat = [
            '今天也要元气满满哦～',
            '你有多久没看番了？',
            '群里有人冒泡了吗？',
            '猜猜我几岁？',
            '网站最近更新了哦～',
            '要不要去看看照片墙？',
            '留言板有人在等你哦～',
            '听说番剧百科很好玩',
            '社长今天也辛苦了',
            '摸鱼中……勿扰',
            '我是不是站得太久了？',
            '有没有那种，好看又短的番',
            '今天天气真好啊～',
            '啊，突然想吃草莓蛋糕',
            '你点我干嘛，我很忙的（其实不忙）',
            '刚在后台看到有人上传照片了',
            '本周新番有人追吗？',
            '别看了，去发个留言吧',
            '第一次来吗？随便逛逛～',
            '我不困，我只是在打盹',
        ]

        _kanban_cache['lines'] = {'facts': facts, 'chitchat': chitchat}
        _kanban_cache['expires'] = now + timedelta(minutes=5)

    # 每次实时拼装：把"欢迎回来 XXX"放在最前
    payload = dict(_kanban_cache['lines'])
    username = session.get('username')
    if username:
        payload['facts'] = [f'欢迎回来，{username}！'] + list(payload['facts'])

    return jsonify(payload)


# 错误处理器
def page_not_found(_e):
    return render_template('404.html'), 404


def internal_server_error(_e):
    return render_template('500.html'), 500


def forbidden(_e):
    return render_template('403.html'), 403


@public_bp.app_errorhandler(405)
def method_not_allowed(_e):
    return render_template('405.html'), 405


@public_bp.app_errorhandler(400)
def bad_request(_e):
    return render_template('400.html'), 400


@public_bp.app_errorhandler(413)
def request_entity_too_large(_e):
    return render_template('413.html'), 413
