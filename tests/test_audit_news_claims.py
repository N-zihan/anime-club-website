"""T5 独立复检 · 针对 t3-news 自我修正后新口径的对抗性验证

覆盖 lead 指定的重点：
  1) handler 转换 `?yt_embed=<ID>` → youtube.com/embed/<ID> 的恶意/畸形输入
  2) animecorner.me 绝不被当成可嵌入域
  3) 服务端二次复核真的独立（伪造 embed=True + 非白名单域 → 不得渲染 iframe）
  4) 语料退避：失败时保留 last-known-good，退避窗口内不再发请求
  5) 模板无 |safe（视频路径）
  6) ★ 语料哨兵值 `_CORPUS_UNAVAILABLE` 的调用方契约（不得让详情页 500）
"""
import pytest

from app import anime_news
from app.anime_news import (
    _youtube_id_from_handler_url, _is_allowed_embed_url, _safe_media_url,
)


# ===========================================================================
# 1) handler 转换：恶意/畸形 ID
# ===========================================================================
@pytest.mark.parametrize('raw,expect', [
    # 合法 ID：应提取
    ('https://animecorner.me/acwp-direct/youtube-iframe-handler.html?yt_embed=T4S-2lR-164', 'T4S-2lR-164'),
    ('https://animecorner.me/acwp-direct/youtube-iframe-handler.html?yt_embed=dQw4w9WgXcQ', 'dQw4w9WgXcQ'),
    ('https://animecorner.me/x.html?yt=abc12345', 'abc12345'),
    ('https://animecorner.me/x.html?v=abc12345', 'abc12345'),
    ('https://animecorner.me/x.html?video_id=abc12345', 'abc12345'),
    # ---- 恶意 / 畸形：必须返回 None ----
    ('https://animecorner.me/x.html?yt_embed=../../evil', None),
    ('https://animecorner.me/x.html?yt_embed=<script>alert(1)</script>', None),
    ('https://animecorner.me/x.html?yt_embed="onload="alert(1)', None),
    ('https://animecorner.me/x.html?yt_embed=a b', None),          # 空格
    ('https://animecorner.me/x.html?yt_embed=ab', None),           # 太短(<6)
    ('https://animecorner.me/x.html?yt_embed=' + 'A' * 50, None),  # 超长(>20)
    ('https://animecorner.me/x.html?yt_embed=abc%22onload', None), # 编码引号
    ('https://animecorner.me/x.html?yt_embed=&', None),
    ('https://animecorner.me/x.html', None),
    ('https://animecorner.me/x.html?yt_embed=abc/def', None),
    ('https://animecorner.me/x.html?yt_embed=abc%3Cscript%3E', None),
])
def test_handler_id_extraction_is_strict(raw, expect):
    got = _youtube_id_from_handler_url(raw)
    assert got == expect, f'{raw!r} → {got!r}，期望 {expect!r}'


def test_handler_id_never_produces_dirty_url():
    """无论输入多脏，转换出的 URL 必须干净且落在 youtube.com。"""
    payloads = [
        '../../evil', '<script>', '"onload="', "x'y", 'a b', 'A' * 99,
        '%22', 'abc%00def', 'abc\ndef',
    ]
    for p in payloads:
        url = f'https://animecorner.me/h.html?yt_embed={p}'
        vid = _youtube_id_from_handler_url(url)
        if vid is None:
            continue
        built = f'https://www.youtube.com/embed/{vid}'
        assert _is_allowed_embed_url(built), f'转换出的 URL 未通过白名单: {built!r}'
        for ch in ('"', "'", '<', '>', '`', ' ', '\x00', '\n'):
            assert ch not in built, f'转换出的 URL 含脏字符: {built!r}'


# ===========================================================================
# 2) animecorner.me（原站域）绝不可嵌入
# ===========================================================================
@pytest.mark.parametrize('url', [
    'https://animecorner.me/acwp-direct/youtube-iframe-handler.html?yt_embed=T4S-2lR-164',
    'https://animecorner.me/embed/x',
    'https://animecorner.me/video/x',
    'https://www.animecorner.me/player/x',
    'https://animecorner.me.evil.test/embed/x',
])
def test_animecorner_never_embeddable(url):
    assert not _is_allowed_embed_url(url), f'原站域被误判为可嵌入: {url}'


def test_handler_url_in_iframe_becomes_youtube_not_origin(monkeypatch):
    """端到端：原文里写 handler iframe，最终 videos 里应是 youtube 而不是原站域。"""
    html = ('<html><head><title>T</title></head><body><article>'
            '<p>This is a sufficiently long filler paragraph used to satisfy the '
            'minimum content length requirement of the article extractor.</p>'
            '<p>Another equally long filler paragraph so that translation and length '
            'checks both pass without any network access at all.</p>'
            '<iframe src="https://animecorner.me/acwp-direct/'
            'youtube-iframe-handler.html?yt_embed=T4S-2lR-164"></iframe>'
            '</article></body></html>')

    class _Res:
        text = html
        status_code = 200

        def raise_for_status(self):
            return None

    monkeypatch.setattr(anime_news, 'safe_get', lambda url, **kw: _Res(), raising=False)
    monkeypatch.setattr(anime_news, '_translate_article', lambda t: t, raising=False)
    anime_news._article_cache.clear()

    result = anime_news.get_article_content('https://news.nanyi-anime-club.top/audit-case')
    assert result is not None
    videos = result.get('videos', [])
    assert videos, 'handler iframe 未被识别为视频'
    for v in videos:
        if isinstance(v, dict):
            assert 'animecorner.me' not in (v.get('url') or ''), \
                f'原站域被当成可嵌入 URL 输出: {v!r}'
            if v.get('embed'):
                assert v['url'].startswith('https://www.youtube.com/embed/'), \
                    f'embed 项不是标准 YouTube 地址: {v["url"]!r}'
                assert _is_allowed_embed_url(v['url']), 'embed 项未通过白名单复核'


# ===========================================================================
# 3) 服务端二次复核：伪造 embed=True + 非白名单域 → 不得渲染 iframe
# ===========================================================================
_FORGED = [
    {'type': 'iframe', 'url': 'https://evil.example.com/embed/x', 'embed': True},
    {'type': 'iframe', 'url': 'javascript:alert(1)', 'embed': True},
    {'type': 'iframe', 'url': 'data:text/html,<script>alert(1)</script>', 'embed': True},
    {'type': 'iframe', 'url': 'https://animecorner.me/embed/x', 'embed': True},
    {'type': 'iframe', 'url': 'https://www.youtube.com:evil.example.com/embed/x', 'embed': True},
]


@pytest.mark.parametrize('forged', _FORGED)
def test_server_recheck_rejects_forged_embed_flag(client, monkeypatch, forged):
    """不信任上游 embed 标志：域名非白名单时绝不能渲染成 iframe。"""
    fake = {
        'title': 'T', 'content': 'c' * 200, 'images': [],
        'videos': [forged],
    }
    monkeypatch.setattr(anime_news, 'get_article_content', lambda url: fake, raising=False)
    resp = client.get('/anime_news/article',
                      query_string={'url': 'https://news.nanyi-anime-club.top/p/1'})
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    import re
    for m in re.finditer(r'<iframe[^>]*>', html, re.I | re.S):
        tag = m.group(0)
        assert 'evil.example.com' not in tag, f'伪造 embed 被渲染成 iframe: {tag}'
        assert 'javascript:' not in tag.lower(), f'伪协议进入 iframe: {tag}'
        assert 'data:text/html' not in tag.lower(), f'data URL 进入 iframe: {tag}'
        assert 'animecorner.me' not in tag, f'原站域进入 iframe: {tag}'


# ===========================================================================
# 4) 语料退避：失败时保留 last-known-good，退避内不再发请求
# ===========================================================================
def _corpus_setup(monkeypatch, second):
    """返回 (calls, get) —— 第一次成功返回语料，之后走 `second` 行为。"""
    good_arts = [{'title': 'Good Article', 'link': 'https://x.test/1',
                  'excerpt': 'e', 'date': '2026-01-01', 'image': '', 'source': 'S',
                  'slug': 'g', 'tags': []}]
    calls = {'n': 0}

    class _Res:
        def __init__(self, payload):
            self._p = payload

        def json(self):
            return self._p

    def fake_get(url, **kw):
        calls['n'] += 1
        if calls['n'] == 1:
            return _Res({'data': good_arts})
        return second()

    monkeypatch.setattr(anime_news.requests, 'get', fake_get)
    monkeypatch.setattr(anime_news, '_translate_batch',
                        lambda ts: {t: t for t in ts}, raising=False)
    anime_news._cache.clear()
    anime_news._corpus_fail_streak = 0
    return calls, good_arts


def _expire_main_corpus_cache():
    """只让主缓存过期，**保留 stale**——这才是「TTL 到期后上游抖动」的正确模拟。"""
    anime_news._cache.pop(anime_news._CORPUS_KEY, None)


def test_corpus_retains_last_known_good_on_exception(monkeypatch):
    """★ 正常路径：上游抛异常时，last-known-good 必须被保留且进入退避。"""
    def second():
        raise TimeoutError('upstream down')

    calls, good = _corpus_setup(monkeypatch, second)

    first = anime_news._get_search_corpus()
    assert first, '第一次应成功返回语料'
    _expire_main_corpus_cache()

    second_result = anime_news._get_search_corpus()
    assert len(second_result) == len(good), \
        f'last-known-good 未被保留（被覆盖成 {second_result!r}）'
    assert anime_news._corpus_backoff_active(), '失败后未进入退避窗口'

    n_before = calls['n']
    anime_news._get_search_corpus()
    assert calls['n'] == n_before, \
        f'退避窗口内仍然打了上游（{n_before} → {calls["n"]}）'


def test_corpus_retains_last_known_good_on_malformed_payload(monkeypatch):
    """结构异常（HTTP 200 但 data 非列表 / 缺 data 键）也必须走 stale + 退避。

    ✅ 2026-10-17 复验通过：t3-news 已把异常路径与结构异常路径统一到
    `_corpus_fail()` 单一出口，原 `return []` 早返回已消除。xfail 标记已摘除。
    """
    def second():
        class _R:
            @staticmethod
            def json():
                return {'data': 'maintenance'}   # 200 但结构异常
        return _R()

    calls, good = _corpus_setup(monkeypatch, second)

    first = anime_news._get_search_corpus()
    assert first
    _expire_main_corpus_cache()

    second_result = anime_news._get_search_corpus()
    assert len(second_result) == len(good), \
        f'结构异常时 last-known-good 未被保留（被覆盖成 {second_result!r}）'
    assert anime_news._corpus_backoff_active(), '结构异常后未进入退避窗口'


def test_corpus_backoff_active_after_failure(monkeypatch):
    """失败后 _corpus_backoff_active 必须为 True。"""
    def boom(url, **kw):
        raise TimeoutError('net down')

    monkeypatch.setattr(anime_news.requests, 'get', boom)
    monkeypatch.setattr(anime_news, '_translate_batch', lambda ts: {}, raising=False)
    anime_news._cache.clear()
    anime_news._corpus_fail_streak = 0

    anime_news._get_search_corpus()
    assert anime_news._corpus_backoff_active(), '失败后未进入退避窗口'

    # 退避窗口内再调用不应再发请求
    calls = {'n': 0}

    def counting(url, **kw):
        calls['n'] += 1
        raise TimeoutError('x')

    monkeypatch.setattr(anime_news.requests, 'get', counting)
    anime_news._get_search_corpus()
    assert calls['n'] == 0, f'退避窗口内仍发起了 {calls["n"]} 次请求'


# ===========================================================================
# 5) 模板无 |safe（只检查真实属性用法，避免误命中注释里的文字）
# ===========================================================================
def test_video_section_has_no_safe_filter():
    import os
    import re
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        'templates', 'anime_news_detail.html')
    src = open(path, encoding='utf-8').read()
    # 去掉 Jinja 注释 {# ... #} 后再查，注释里提到 |safe 是安全约定说明
    body = re.sub(r'\{#.*?#\}', '', src, flags=re.S)
    assert '|safe' not in body, '详情页模板（注释外）出现了 |safe'
    # 反向确认：iframe 的 src 用的是 media_url 过滤器且以 embed_ok 为条件
    assert '|media_url' in body, '未使用 media_url 过滤器净化视频 URL'
    assert 'embed_ok' in body, '未使用 embed_ok 做白名单判定'


# ===========================================================================
# 6) ★ 语料哨兵值 _CORPUS_UNAVAILABLE 的调用方契约
#    t3-news 引入哨兵对象后，find_article_meta() 第 428 行仍直接 for 循环它，
#    实测 TypeError → 新闻详情页 500。修好后本用例会自动 XPASS。
# ===========================================================================
def _sentinel():
    s = getattr(anime_news, '_CORPUS_UNAVAILABLE', None)
    if s is None:
        pytest.skip('尚未引入 _CORPUS_UNAVAILABLE 哨兵')
    return s


def test_sentinel_is_iterable_for_legacy_consumers():
    """哨兵对象必须可迭代（返回空），否则任何漏改的旧消费方都会 TypeError。"""
    s = _sentinel()
    try:
        items = list(s)
    except TypeError as e:
        pytest.fail(f'哨兵不可迭代，旧调用方（find_article_meta）会崩: {e}')
    assert items == [], f'哨兵迭代应为空: {items!r}'


def test_find_article_meta_survives_unavailable_corpus(monkeypatch):
    """语料不可用时 find_article_meta 必须安全降级返回 None，不得抛 TypeError。

    ✅ 复验通过：哨兵已实现空序列协议（__iter__/__len__/__bool__/__getitem__），
    且 find_article_meta 另加显式 `is _CORPUS_UNAVAILABLE` 双保险。xfail 已摘除。
    """
    s = _sentinel()
    monkeypatch.setattr(anime_news, '_get_search_corpus', lambda: s, raising=False)
    result = anime_news.find_article_meta('https://news.nanyi-anime-club.top/post/1')
    assert result is None, f'期望降级为 None，实际 {result!r}'


def test_article_page_returns_200_when_corpus_unavailable(client, monkeypatch):
    """★ 端到端：语料不可用但正文抓取成功时，详情页必须 200 而不是 500。

    ✅ 复验通过：原 TypeError('_CorpusUnavailable' object is not iterable) 已消除。
    xfail 已摘除。注意必须同时打桩 safe_get，否则会真的联网抓取。
    """
    s = _sentinel()
    payload = {'title': 'T', 'content': 'c' * 200, 'images': [], 'videos': []}

    class _Res:
        text = '<html><head><title>T</title></head><body></body></html>'
        status_code = 200

        def raise_for_status(self):
            return None

    monkeypatch.setattr(anime_news, 'get_article_content', lambda url: payload, raising=False)
    monkeypatch.setattr(anime_news, '_get_search_corpus', lambda: s, raising=False)
    monkeypatch.setattr(anime_news, 'safe_get', lambda url, **kw: _Res(), raising=False)

    resp = client.get('/anime_news/article',
                      query_string={'url': 'https://news.nanyi-anime-club.top/post/1'})
    assert resp.status_code == 200, f'详情页在哨兵态下返回 HTTP {resp.status_code}'
