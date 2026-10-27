"""
动漫社官网 · Flask 应用工厂模块
============================================

本模块是整个网站的核心入口，负责创建和配置 Flask 应用实例
它通过 create_app() 工厂函数完成以下工作：

1. 加载环境变量（数据库连接、密钥、版本号等）
2. 初始化 SQLAlchemy 数据库连接
3. 注册所有蓝图模块（认证、公共、用户、管理后台）
4. 注册全局错误处理器（400/403/404/405/413/500）
5. 设置登录拦截器（未登录用户自动跳转至登录页）
6. 注入模板全局变量（版本号、用户权限标识）

这种工厂模式使得应用可以被多次实例化，便于测试和扩展
"""

import os

from dotenv import load_dotenv
from flask import Flask, session, request, redirect, url_for, flash, render_template
from flask_wtf import CSRFProtect
from flask_wtf.csrf import CSRFError

from .admin import admin_bp
from .auth import auth_bp
from .config import PWA_THEME_COLOR, PWA_BACKGROUND_COLOR
from .models import db, User
from .preferences import get_preferences
from .public import (
    public_bp, page_not_found, internal_server_error, forbidden,
    method_not_allowed, bad_request, request_entity_too_large,
)
from .user import user_bp
from .version import resolve_version

load_dotenv()


def create_app():
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    app = Flask(
        __name__,
        template_folder=os.path.join(base_dir, 'templates'),
        static_folder=os.path.join(base_dir, 'static'),
        static_url_path='/static'
    )
    app.secret_key = os.getenv('SECRET_KEY')
    if not app.secret_key:
        raise RuntimeError("SECRET_KEY must be set in environment variables")

    # ====== Session Cookie 安全配置 ======
    # 原实现依赖 FLASK_ENV == 'production'，而全项目从未设置过 FLASK_ENV
    # （.env.example / vercel.json / README 都没有），Secure 标志实际上永远是关的。
    # 改为：Vercel（HTTPS）上默认开启，本地开发默认关闭，可用环境变量显式覆盖。
    _cookie_secure_default = '1' if os.getenv('VERCEL') else '0'
    app.config.update(
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE='Lax',
        SESSION_COOKIE_SECURE=os.getenv('SESSION_COOKIE_SECURE', _cookie_secure_default) == '1',
        # 上传体积上限：由框架在解析请求体之前拦截。
        # 原先三处上传（头像/提名图/照片墙）都没有兜底限制，且头像那处只在带
        # Content-Length 时检查，chunked 请求可绕过，file.read() 会把整个请求体读进内存。
        MAX_CONTENT_LENGTH=int(os.getenv('MAX_CONTENT_LENGTH', str(4 * 1024 * 1024))),  # 4MB
    )
    # ==========================================

    # 数据库配置
    DATABASE_URL = os.getenv('DATABASE_URL') # pylint: disable=invalid-name
    if not DATABASE_URL:
        app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///site.db'
    else:
        app.config['SQLALCHEMY_DATABASE_URI'] = DATABASE_URL
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

    # ====== 连接池参数（Serverless 关键） ======
    # 原实现完全没配 SQLALCHEMY_ENGINE_OPTIONS：Postgres 走 SQLAlchemy 默认
    # QueuePool(pool_size=5, max_overflow=10)，单个 Vercel 实例最多开 15 条连接，
    # 多实例并发叠加会打满 Supabase 免费计划的连接上限，直接导致整站 500；
    # 且线上 DATABASE_URL 走 6543 端口（Supavisor 事务池），长连接被复用时
    # 还会触发 prepared statement 报错。
    # SQLite 不支持 pool_size/max_overflow（走 StaticPool/SingletonThreadPool），
    # 传了会直接报错，所以按 URL 前缀分流：只有 Postgres 才注入池参数。
    if DATABASE_URL and DATABASE_URL.split(':', 1)[0] in ('postgres', 'postgresql'):
        app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
            'pool_pre_ping': True,   # 免费计划连接可能被服务端回收，取用前先探活
            'pool_size': 1,          # Serverless 每实例只需极少连接
            'max_overflow': 2,
            'pool_recycle': 300,     # 早于 Supabase 侧空闲超时主动回收
        }
    # =========================================

    db.init_app(app)

    # ====== CSRF 保护 ======
    csrf = CSRFProtect()
    csrf.init_app(app)
    # CSRF 保护已启用，所有环境（包括测试）都应保持保护
    # 如需在测试中禁用，请通过环境变量 DISABLE_CSRF_FOR_TESTS='1' 设置
    if os.getenv('DISABLE_CSRF_FOR_TESTS') == '1':
        app.config['WTF_CSRF_ENABLED'] = False
        print('⚠️  WARNING: CSRF 保护已根据环境变量 DISABLE_CSRF_FOR_TESTS 设置，生产环境中应始终开启')
    # =============================

    # 注册蓝图
    app.register_blueprint(auth_bp)
    app.register_blueprint(public_bp)
    app.register_blueprint(user_bp)
    app.register_blueprint(admin_bp)
    
    # 注册错误处理器（400/405/413 的自定义页面此前已写好，但从未注册）
    app.register_error_handler(404, page_not_found)
    app.register_error_handler(500, internal_server_error)
    app.register_error_handler(403, forbidden)
    app.register_error_handler(400, bad_request)
    app.register_error_handler(405, method_not_allowed)
    app.register_error_handler(413, request_entity_too_large)

    CLUB_NAME = os.getenv('CLUB_NAME', '动漫社') # pylint: disable=invalid-name

    @app.context_processor
    def inject_club_name():
        return {'club_name': CLUB_NAME}

    @app.context_processor
    def inject_config():
        return {
            'config': {
                'PWA_THEME_COLOR': PWA_THEME_COLOR,
                'PWA_BACKGROUND_COLOR': PWA_BACKGROUND_COLOR
            }
        }

    @app.errorhandler(CSRFError)
    def handle_csrf_error(_e):
        # 不要在这里 session.clear()：否则任何跨站 POST 都能把用户强制登出。
        return render_template('400.html'), 400

    # 静态资源扩展名 → 可缓存
    _STATIC_CACHE_EXTS = (
        '.css', '.js', '.mjs', '.png', '.jpg', '.jpeg', '.webp', '.gif', '.svg',
        '.ico', '.woff', '.woff2', '.ttf', '.map',
    )
    # 内容极少变化的公开文件
    _PUBLIC_CACHE_PATHS = ('/robots.txt', '/sitemap.xml', '/manifest.json')

    @app.after_request
    def add_security_headers(response):
        """安全响应头 + 静态资源缓存。

        Vercel 上所有请求（包括 /static/）都经过 Flask 函数，浏览器/边缘不缓存的话
        每次打开页面都要回源一次（实测 150~200ms/个）。带 ?v= 的 CSS/JS 的 URL 会随
        版本变化，可以放心 immutable；无版本号的图片等给 1 天。
        """
        response.headers.setdefault('X-Content-Type-Options', 'nosniff')
        response.headers.setdefault('X-Frame-Options', 'SAMEORIGIN')
        response.headers.setdefault('Referrer-Policy', 'strict-origin-when-cross-origin')
        response.headers.setdefault('Permissions-Policy',
                                    'geolocation=(), microphone=(), camera=()')

        path = request.path
        if response.status_code == 200 and path.startswith('/static/'):
            ext = os.path.splitext(path)[1].lower()
            if ext in _STATIC_CACHE_EXTS:
                if request.args.get('v'):
                    # 模板里所有 CSS/JS 都带 ?v=<版本号>，URL 变了才会重新请求
                    cache = 'public, max-age=31536000, s-maxage=31536000, immutable'
                else:
                    cache = 'public, max-age=86400, s-maxage=86400'
                response.headers['Cache-Control'] = cache
                # Vercel 边缘缓存：函数响应默认不缓存，显式声明后静态资源由边缘直接返回
                response.headers['Vercel-CDN-Cache-Control'] = cache
        elif response.status_code == 200 and path in _PUBLIC_CACHE_PATHS:
            # 爬虫抓 robots/sitemap 不用每次回源；1 小时足够新
            cache = 'public, max-age=3600, s-maxage=3600'
            response.headers['Cache-Control'] = cache
            response.headers['Vercel-CDN-Cache-Control'] = cache
        return response

    @app.context_processor
    def inject_vote_poll():
        """投票页轮询间隔（秒），可用 VOTE_POLL_SECONDS 调整，范围 3~60。"""
        try:
            seconds = int(os.getenv('VOTE_POLL_SECONDS', '10'))
        except ValueError:
            seconds = 10
        return {'vote_poll_seconds': max(3, min(seconds, 60))}

    @app.context_processor
    def inject_supabase_host():
        """头像/照片在 Supabase Storage 的跨域域名，提前 preconnect 省一次建连。"""
        url = os.getenv('SUPABASE_URL', '') or ''
        host = url.split('://', 1)[-1].split('/', 1)[0] if url else ''
        return {'supabase_host': host}

    # 允许搜索引擎收录的只读页面（按需增删；不要把写接口放进来）
    crawlable_routes = [
        'public.splash',
        'public.about',
        'public.history',
        'public.activities',
        'public.anime_resources',
        'public.anime_news',
        'public.contest_rules',
        'public.robots',
        'public.manifest',
    ]

    # 登录拦截器
    @app.before_request
    def require_login():

        # 这些端点完全不碰 session：提前返回，响应就不会带 `Vary: Cookie`，
        # Vercel 边缘才愿意缓存 /static/、robots、sitemap、manifest（否则每次回源函数）。
        if request.endpoint in ('static', 'public.robots', 'public.sitemap',
                                'public.manifest', 'public.favicon', 'public.health'):
            return None

        # 放行搜索引擎爬虫：**仅限 GET**，且仅限上面的只读白名单。
        # 旧实现只要 UA 里含 'Googlebot' 就整段跳过登录判断，而 UA 完全可以伪造，
        # 等于给所有路由（含 POST /board、/api/*）开了一个匿名后门。
        ua = request.headers.get('User-Agent', '')
        bot_agents = [
            'bingbot',  # Bing
            'Baiduspider',  # 百度
            'Googlebot',  # Google
            '360Spider',  # 360搜索
            'Sogou web spider',  # 搜狗（注意有空格）
            'YisouSpider',  # 神马搜索
            'yisouspider'  # 神马搜索（小写变体）
        ]
        if (request.method == 'GET'
                and request.endpoint in crawlable_routes
                and any(bot in ua for bot in bot_agents)):
            return None

        public_routes = [
            'auth.login',
            'auth.register',
            'auth.send_register_code',
            'static',
            'public.splash',
            'auth.forgot_password',
            'auth.forgot_password_bind',
            'auth.reset_password',
            'auth.forgot_bind_send_code',
            'auth.forgot_bind_verify',
            'public.ai_commentary',
            'public.ai_predict',
            'public.manifest',
            'public.robots',
            'public.sitemap',
            'public.service_worker',
            # 新闻相关API（允许匿名访问）
            'public.api_anime_news',
            'public.api_anime_news_search',
            'public.api_news_image',
            'public.api_anime_news_detail',
            'public.anime_news',
            'public.anime_news_article',
            'public.anime_news_rss',
            'public.offline',
            'public.weekly_digest_cron',
            'public.weekly_digest_resume',
            'public.digest_unsubscribe',
            'public.health',
            'public.favicon',
            'public.share_home',
            'public.share_contest',
            # 设置页只读写本机偏好，不含账号数据，匿名访客也可使用
            'public.settings',
        ]
        # request.endpoint 为 None 表示没有匹配任何路由：应交给 404 处理器，
        # 而不是把匿名访客重定向到登录页（原先会让所有 404 看起来像 302）。
        if (not session.get('user_id') and request.endpoint is not None
                and request.endpoint not in public_routes and request.endpoint != 'static'):
            return redirect(url_for('auth.login'))
        return None

    # 版本号：优先读 git tag（VERSION 文件 / git describe），解析规则见 app/version.py
    app.config['APP_VERSION'] = resolve_version()

    @app.context_processor
    def inject_version():
        return {'app_version': app.config['APP_VERSION']}

    @app.context_processor
    def inject_user():
        role = session.get('user_role')
        is_admin = role in ('owner', 'staff')
        return {'is_admin': is_admin}

    @app.context_processor
    def inject_user_preferences():
        """当前用户的账号级偏好（未登录返回空 dict，模板用 get/默认值兜底）"""
        user_id = session.get('user_id')
        if not user_id:
            return {'user_prefs': {}}
        return {'user_prefs': get_preferences(db.session.get(User, user_id))}

    @app.context_processor
    def inject_avatar_helper():
        def avatar_url(user):
            if user and getattr(user, 'avatar_url', None):
                return user.avatar_url
            if user:
                return url_for('user.get_avatar', user_id=user.id)
            return ("data:image/svg+xml;utf8,"
                    "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 40 40'>"
                    "<rect width='40' height='40' fill='%23cbd5e1'/>"
                    "<circle cx='20' cy='15' r='7' fill='white'/>"
                    "<ellipse cx='20' cy='33' rx='12' ry='9' fill='white'/>"
                    "</svg>")
        return {'avatar_url': avatar_url}

    # ====== 自动注入 CSRF token 到所有模板 ======
    @app.context_processor
    def inject_csrf_token():
        from flask_wtf.csrf import generate_csrf
        return {'csrf_token': generate_csrf}

    return app
