"""T5 独立复检 · 番剧新闻详情页空值/降级兜底 + 模板层 XSS 验证

Lead 追加要求：
  a) T3 实现后，'videos' 字段为空/缺失时详情页模板不得崩；
  b) get_article_content 返回 None 时详情页要有明确降级行为；
  c) 模板层对非白名单视频 URL 绝不能输出成可执行 iframe。

本文件用**真实 Flask 路由 + 真实 Jinja 环境（含 base.html 继承）**验证，
而不是孤立 Jinja 环境，避免「模板单独渲染 OK 但真实页面 500」的漏检。

★ 本文件曾在 2026-10-17 之间被外部删除（只剩 .pyc），已由 T5 重建。
"""
import pytest

from app import anime_news, public


@pytest.fixture
def patched_detail(monkeypatch):
    """打桩抓取层，避免任何外网请求。

    ★ 必须同时打桩两个名字：public.py 顶层 `from .anime_news import get_article_content`
    已把函数对象绑定进 app.public 的命名空间，只打桩 anime_news.* 对详情页无效
    （旧版 fixture 正是如此，这些用例其实一直在跑「真实抓取失败」的降级路径，
    所以断言 200 能过、断言内容就过不了）。
    """
    def _set(payload):
        monkeypatch.setattr(anime_news, 'get_article_content', lambda url: payload,
                            raising=False)
        monkeypatch.setattr(public, 'get_article_content', lambda url: payload,
                            raising=False)
        # 详情页还会用 find_article_meta 找标题元数据；一并不走网络，保证用例 hermetic
        monkeypatch.setattr(public, 'find_article_meta', lambda url: None, raising=False)
    return _set


_VALID_URL = 'https://news.nanyi-anime-club.top/post/1'


def test_route_renders_200_when_fetch_returns_none(client, patched_detail):
    """抓取失败（None）时详情页必须降级渲染而不是 500。"""
    patched_detail(None)
    resp = client.get('/anime_news/article', query_string={'url': _VALID_URL})
    assert resp.status_code == 200, f'降级失败，HTTP {resp.status_code}'
    body = resp.get_data(as_text=True)
    assert 'anime_news' in body or '返回' in body


def test_route_renders_200_when_videos_key_missing(client, patched_detail):
    """T3 之前的返回结构（无 videos 键）也必须能渲染。"""
    patched_detail({'title': 'T', 'content': 'x' * 200, 'images': []})
    resp = client.get('/anime_news/article', query_string={'url': _VALID_URL})
    assert resp.status_code == 200


@pytest.mark.parametrize('videos', [
    [],
    None,
    [{}],
    [{'url': None}],
    [{'url': ''}],
    [{'src': 'https://www.youtube.com/embed/a'}],
])
def test_route_renders_200_for_degenerate_videos(client, patched_detail, videos):
    """videos 为 空/None/元素缺字段 时不得抛异常。"""
    patched_detail({'title': 'T', 'content': 'y' * 200, 'images': [], 'videos': videos})
    resp = client.get('/anime_news/article', query_string={'url': _VALID_URL})
    assert resp.status_code == 200, f'videos={videos!r} 导致 HTTP {resp.status_code}'


def test_route_rejects_invalid_url(client):
    """url 参数缺失或非公网时必须是 404/400，不得进入抓取。"""
    assert client.get('/anime_news/article').status_code == 404
    assert client.get('/anime_news/article',
                      query_string={'url': 'javascript:alert(1)'}).status_code == 404
    assert client.get('/anime_news/article',
                      query_string={'url': 'http://127.0.0.1:8000/x'}).status_code == 404


def test_api_detail_returns_error_when_fetch_none(client, patched_detail):
    """API 在抓取失败时应返回错误 JSON 而不是 500。"""
    patched_detail(None)
    resp = client.get('/api/anime/news-detail', query_string={'url': _VALID_URL})
    assert resp.status_code in (404, 502, 503), f'意外状态码 {resp.status_code}'
    assert resp.is_json, 'API 必须返回 JSON 错误体'


def test_api_detail_rejects_private_url(client):
    """API 必须挡住内网地址（SSRF）。"""
    for bad in ('http://169.254.169.254/latest/meta-data/',
                'http://localhost:5000/',
                'file:///etc/passwd'):
        resp = client.get('/api/anime/news-detail', query_string={'url': bad})
        assert resp.status_code == 400, f'{bad} 未被挡住，HTTP {resp.status_code}'


# ---------------------------------------------------------------------------
# 模板层：即使解析层漏了，模板也绝不能把非白名单 URL 渲染成可执行 iframe
# ---------------------------------------------------------------------------
def _render_detail(app, **ctx):
    from flask import render_template
    with app.test_request_context('/anime_news/article', query_string={'url': _VALID_URL}):
        return render_template('anime_news_detail.html', **ctx)


@pytest.mark.parametrize('evil', [
    'https://evil.example.com/embed/x',
    'javascript:alert(1)',
    'data:text/html,<script>alert(1)</script>',
    'https://attacker.test/x.mp4',
])
def test_template_never_emits_evil_iframe(app, evil):
    """把恶意 URL 塞进 videos，模板输出里不得出现它的 iframe。"""
    html = _render_detail(
        app, article=None, url=_VALID_URL, content='c' * 200, images=[],
        source_zh='测试', videos=[{'url': evil, 'type': 'iframe'}],
    )
    lowered = html.lower()
    assert 'javascript:alert(1)' not in lowered, '模板直接输出了 javascript: 伪协议'
    assert 'data:text/html' not in lowered, '模板直接输出了 data: URL'
    # 若 evil 被渲染，只能作为 <a href> 外链，不能是 <iframe src>
    import re
    for m in re.finditer(r'<iframe[^>]*>', html, re.I | re.S):
        tag = m.group(0)
        assert 'evil.example.com' not in tag, f'非白名单域被渲染成 iframe: {tag}'
        assert 'javascript:' not in tag.lower(), f'iframe 中含伪协议: {tag}'
        assert 'attacker.test' not in tag, f'非白名单域被渲染成 iframe: {tag}'


def test_template_escapes_video_url_in_href(app):
    """外链形式输出时，URL 必须被 Jinja 转义（双引号不得逃逸）。"""
    evil = 'https://evil.example.com/x" onload="alert(1)'
    html = _render_detail(
        app, article=None, url=_VALID_URL, content='c' * 200, images=[],
        source_zh='测试', videos=[{'url': evil, 'type': 'video'}],
    )
    assert 'onload="alert(1)"' not in html, '属性逃逸成功：onload 未被转义'
    assert '" onload=' not in html, '属性逃逸成功：引号未转义'


# ---------------------------------------------------------------------------
# 阶段 C：语言与翻译 —— 详情页同时输出译文块与原文块，由 html[data-news-lang] 决定显示哪份
# ---------------------------------------------------------------------------
def test_detail_page_renders_both_language_blocks(client, patched_detail):
    """正文接口给了原文时，模板要同时渲染译文块与原文块（默认 CSS 只显示译文）。"""
    patched_detail({
        'title': 'Original Title',
        'content': '中文第一段\n\n中文第二段',
        'content_original': 'First paragraph\n\nSecond paragraph',
        'images': [],
    })
    resp = client.get('/anime_news/article', query_string={'url': _VALID_URL})
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'news-lang-block news-lang-zh' in body
    assert 'news-lang-block news-lang-orig' in body
    assert 'news-orig-body' in body
    assert '中文第一段' in body, '译文正文缺失'
    assert 'First paragraph' in body, '原文正文缺失，「仅原文 / 双语对照」会失效'


def test_detail_page_falls_back_to_translated_body_without_original(client, patched_detail):
    """旧缓存/旧结构没有 content_original 时，原文块回落到译文，不能出现空白正文。"""
    patched_detail({'title': 'T', 'content': 'only translated body text', 'images': []})
    resp = client.get('/anime_news/article', query_string={'url': _VALID_URL})
    body = resp.get_data(as_text=True)
    assert resp.status_code == 200
    assert body.count('only translated body text') >= 2, '原文块空掉了'


def test_detail_page_escapes_original_text(client, patched_detail):
    """原文块走 Jinja 自动转义，不能因为「原文」就绕过 XSS 防线。"""
    patched_detail({
        'title': 'T',
        'content': '译文正文' * 60,
        'content_original': '<script>alert(1)</script>',
        'images': [],
    })
    resp = client.get('/anime_news/article', query_string={'url': _VALID_URL})
    body = resp.get_data(as_text=True)
    assert resp.status_code == 200
    assert '<script>alert(1)</script>' not in body, '原文块未转义'


def test_detail_page_warns_when_translation_failed(client, patched_detail):
    """翻译服务不可用/未配置时，详情页必须显式提示「当前显示的是原文」"""
    patched_detail({
        'title': 'T',
        'content': 'English only paragraph text',
        'content_original': 'English only paragraph text',
        'images': [],
        'status': 'translated_failed',
        'reason': '翻译服务不可用，当前显示原文',
    })
    resp = client.get('/anime_news/article', query_string={'url': _VALID_URL})
    body = resp.get_data(as_text=True)
    assert resp.status_code == 200
    assert 'news-translate-notice' in body
    assert '当前显示的是原文' in body


def test_detail_page_has_no_warning_when_translation_ok(client, patched_detail):
    patched_detail({
        'title': 'T',
        'content': '这里是中文译文正文',
        'content_original': 'Here is the English body text',
        'images': [],
        'status': 'ok',
    })
    body = client.get('/anime_news/article', query_string={'url': _VALID_URL}).get_data(as_text=True)
    assert 'news-translate-notice' not in body
