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

from flask import Blueprint, render_template, request, redirect, url_for, session, flash, jsonify, Response, abort
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
# 视频代理相关死代码已下线（tab 保留数据表，仅停止注册路由）。
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
    prepare_group_round_data, current_group_round, current_knockout_sub_round)
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

from .public_bp import public_bp
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
                "purpose": "any"
            },
            {
                "src": "/static/images/icon-512.png",
                "sizes": "512x512",
                "type": "image/png",
                "purpose": "any"
            },
            {
                "src": "/static/images/icon-512-maskable.png",
                "sizes": "512x512",
                "type": "image/png",
                "purpose": "maskable"
            }
        ]
    })


_ROBOTS_RULES = """# 公开页面与静态资源必须允许抓取：微信 / QQ 的分享卡片爬虫要能访问页面和分享图，
# 才能生成缩略图。注意不要改成 "Disallow: /"，否则分享图会被一起拦掉。

User-agent: *
Disallow: /admin
Disallow: /api/
Disallow: /notifications
Disallow: /profile
Disallow: /settings
Disallow: /board
Disallow: /reply
Disallow: /contest
Disallow: /digest

# 社交平台抓取器显式放行（分享卡片）
User-agent: MicroMessenger
Disallow:

User-agent: WeChat
Disallow:

User-agent: QQ
Disallow:

User-agent: QQBrowser
Disallow:

User-agent: MQQBrowser
Disallow:

User-agent: Twitterbot
Disallow:

User-agent: facebookexternalhit
Disallow:

User-agent: TelegramBot
Disallow:

User-agent: Discordbot
Disallow:
"""


@public_bp.route('/robots.txt')
def robots():
    """动态生成 robots.txt：Sitemap 用当前请求域名拼接，不写死任何线上域名。"""
    body = _ROBOTS_RULES + f'\nSitemap: {request.url_root}sitemap.xml\n'
    return Response(body, mimetype='text/plain')


@public_bp.route('/sitemap.xml')
def sitemap():
    """动态生成 sitemap.xml：loc 全部按当前请求域名拼接，换域名 / 多域名部署都能自动适配。"""
    from xml.sax.saxutils import escape

    base = request.url_root
    paths = ['', 'login', 'anime_news', 'settings', 'share']
    urls = [f'{base}{path}' for path in paths]
    try:
        contests = Contest.query.filter(
            Contest.status.in_(['open', 'group_stage', 'knockout'])).all()
    except Exception:  # pragma: no cover - 数据库异常时至少返回基础公开页，sitemap 不能 500
        contests = []
    urls.extend(f'{base}share/contest/{contest.id}' for contest in contests)

    lines = ['<?xml version="1.0" encoding="UTF-8"?>',
             '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    lines.extend(f'  <url><loc>{escape(url)}</loc></url>' for url in urls)
    lines.append('</urlset>')
    return Response('\n'.join(lines) + '\n', mimetype='application/xml')


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


# 子模块：路由注册在同一 public 蓝图上（必须放在本文件路由定义之后导入）
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


from . import public_board, public_contest, public_misc, public_share  # noqa: E402,F401  pylint: disable=wrong-import-position,unused-import
