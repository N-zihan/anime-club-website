"""
动漫社官网 · 动漫新闻数据源
====================================
从自部署的 AniNewsAPI 聚合动漫新闻，使用阿里云机器翻译。
"""
import base64
import hashlib
import hmac
import html
import json
import os
import re
import threading
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from urllib.parse import parse_qs, quote, urljoin, urlsplit

import requests
from bs4 import BeautifulSoup

from .utils import safe_get

ANI_NEWS_API = 'https://news.nanyi-anime-club.top/'

# ============================================================
# 进程内缓存：容量上限 + 锁
# ============================================================
# ★ 注意：这些缓存是**模块级全局**的，在 Vercel Serverless 下**每个实例各有一份**，
#   实例被回收即丢失。因此缓存命中率有限（冷启动必然回源一次），这是平台特性
#   而不是 bug；但也正因为实例内存只有 2GB、且缓存键包含用户可控的搜索词
#   （如 f'search:{keyword}:{limit}'），**必须**有容量上限：
#   否则匿名用户不断换关键词就能把实例内存刷爆。
#   _cache / _translate_cache 的键和值都可能来自用户输入，写入前一律过 _prune()。
_CACHE_MAX = 500          # 列表/搜索/语料等结果缓存上限
_TRANSLATE_MAX = 2000     # 翻译缓存上限（键是原文，值含译文，占用较大）
_ARTICLE_MAX = 200        # 正文缓存上限（每篇正文+图片列表都不小）

# dict 在多线程下并发读改写会抛
# "dictionary changed size during iteration"（search_news / _get_search_corpus
# 都用 ThreadPoolExecutor 并发跑），所有读改写一律持锁。
_cache_lock = threading.RLock()

_cache = {}
_CACHE_TTL = 1800

_translate_cache = {}  # 翻译缓存（有上限，进程重启后清空）

# 翻译缓存命中计数（便于验证「按页懒翻译」没有被反复触发、也没退化成全量翻译）
_translate_stats = {'cache_hit': 0, 'cache_miss': 0, 'api_calls': 0}

_article_cache = {}
_ARTICLE_TTL = 172800


def _prune(cache, limit):
    """把缓存裁剪到 limit 条以内。

    淘汰策略：优先删「已过期」的条目（省内存且不影响正确性），仍然超限时按
    插入顺序删最旧的一批（dict 保序，等价于 FIFO）。不引入额外依赖。
    调用方需自行持有 _cache_lock。
    """
    if len(cache) <= limit:
        return
    now = datetime.now()
    # 1) 先清已过期（值是 (data, expires) 元组，或退避用的裸 datetime）
    expired = []
    for key, value in list(cache.items()):
        expires = value[1] if isinstance(value, tuple) and len(value) == 2 else value
        if isinstance(expires, datetime) and expires < now:
            expired.append(key)
    for key in expired:
        cache.pop(key, None)
    if len(cache) <= limit:
        return
    # 2) 仍然超限，从最旧的开始删，一次删到 limit 的 90%，避免每次写入都裁剪
    target = max(1, int(limit * 0.9))
    for key in list(cache.keys())[:max(0, len(cache) - target)]:
        cache.pop(key, None)


def _cache_set(cache, key, value, limit):
    """线程安全地写缓存，并强制容量上限"""
    with _cache_lock:
        cache[key] = value
        _prune(cache, limit)


def _cache_get(cache, key, default=None):
    """线程安全地读缓存"""
    with _cache_lock:
        return cache.get(key, default)


def _cache_pop(cache, key):
    with _cache_lock:
        cache.pop(key, None)


def _cache_keys(cache):
    """线程安全地取键快照（用于遍历，避免并发修改崩溃）"""
    with _cache_lock:
        return list(cache.keys())


def _cache_items(cache):
    """线程安全地取 (键, 值) 快照"""
    with _cache_lock:
        return list(cache.items())

_UA = 'Mozilla/5.0 (compatible; nanyi-anime-club/1.0)'
# 抓取原文页用的真实浏览器 UA：部分站点对 "compatible; bot" 一律 403/断连
_UA_BROWSER = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
               '(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36')
# 原文抓取超时：实测多数站点 1-3s 内返回，15s 只是把偶发断连拖成用户可感的卡顿
_ARTICLE_FETCH_TIMEOUT = 10

# 阿里云机器翻译端点
_ALIYUN_MT_ENDPOINT = 'https://mt.aliyuncs.com/'
_ALIYUN_SINGLE_MAX = 4500  # 单次请求最大字符数（保守取值）


# ============================================================
# 阿里云翻译
# ============================================================
def _aliyun_call(text, source='en', target='zh'):
    """单次调用阿里云机器翻译 API，返回翻译文本（失败返回 None）"""
    if not text or not text.strip():
        return ''

    ak_id = os.getenv('ALIBABA_CLOUD_ACCESS_KEY_ID')
    ak_secret = os.getenv('ALIBABA_CLOUD_ACCESS_KEY_SECRET')
    if not ak_id or not ak_secret:
        return None

    params = {
        'Action': 'TranslateGeneral',
        'Version': '2018-10-12',
        'Format': 'JSON',
        'AccessKeyId': ak_id,
        'SignatureMethod': 'HMAC-SHA1',
        'SignatureVersion': '1.0',
        'SignatureNonce': str(uuid.uuid4()),
        'Timestamp': datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ'),
        'FormatType': 'text',
        'SourceLanguage': source,
        'TargetLanguage': target,
        'SourceText': text,
        'Scene': 'general',
    }

    # 阿里云签名算法
    sorted_params = sorted(params.items())
    canonicalized = '&'.join(
        f'{quote(k, safe="")}={quote(str(v), safe="")}'
        for k, v in sorted_params
    )
    string_to_sign = 'GET&%2F&' + quote(canonicalized, safe='')
    signature = base64.b64encode(
        hmac.new(
            (ak_secret + '&').encode('utf-8'),
            string_to_sign.encode('utf-8'),
            hashlib.sha1,
        ).digest()
    ).decode('utf-8')

    params['Signature'] = signature

    _translate_stats['api_calls'] += 1

    try:
        res = requests.get(_ALIYUN_MT_ENDPOINT, params=params, timeout=8)
        data = res.json()
        if data.get('Code') == '200':
            return data.get('Data', {}).get('Translated', '')
        print(f'>>> 阿里云翻译错误: code={data.get("Code")} msg={data.get("Message")}')
        return None
    except Exception as e:
        print(f'>>> 阿里云请求失败: {e}')
        return None


def _translate_batch(texts):
    """批量翻译（英→中），带缓存。
    texts: list[str]，返回 {原文: 译文}。
    """
    if not texts:
        return {}

    result = {}
    to_translate = []

    for t in texts:
        if not t or not t.strip():
            result[t] = t
            continue
        cached = _cache_get(_translate_cache, t)
        if cached:
            result[t] = cached
            _translate_stats['cache_hit'] += 1
        else:
            to_translate.append(t)
            _translate_stats['cache_miss'] += 1

    if not to_translate:
        return result

    # 阿里云没有原生批量接口，用 \n 拼接实现伪批量
    # 按字符数分批，防止单次请求超长
    batches = []
    current = []
    current_len = 0

    for t in to_translate:
        # 单条太长，单独处理
        if len(t) > _ALIYUN_SINGLE_MAX:
            if current:
                batches.append(current)
                current = []
                current_len = 0
            batches.append([t])
            continue

        if current_len + len(t) + 1 > _ALIYUN_SINGLE_MAX:
            batches.append(current)
            current = [t]
            current_len = len(t)
        else:
            current.append(t)
            current_len += len(t) + 1

    if current:
        batches.append(current)

    def _run_batch(batch):
        """翻译单个批次，返回 {原文: 译文}"""
        out = {}
        # 单条超长的情况，直接单独调用
        if len(batch) == 1 and len(batch[0]) > _ALIYUN_SINGLE_MAX:
            # 截断处理（超过 4500 字符的文本很少见）
            truncated = batch[0][:_ALIYUN_SINGLE_MAX]
            translated = _aliyun_call(truncated)
            if translated:
                out[batch[0]] = translated
                _cache_set(_translate_cache, batch[0], translated, _TRANSLATE_MAX)
            else:
                out[batch[0]] = batch[0]
            return out

        joined = '\n'.join(batch)
        translated = _aliyun_call(joined)

        if translated is None:
            # 调用失败，原样返回
            for t in batch:
                out[t] = t
            return out

        lines = translated.split('\n')

        # 行数不匹配时，降级为逐条翻译
        if len(lines) != len(batch):
            print(f'>>> 行数不匹配（{len(lines)} vs {len(batch)}），降级逐条翻译')
            for t in batch:
                single = _aliyun_call(t)
                zh = single if single else t
                out[t] = zh
                _cache_set(_translate_cache, t, zh, _TRANSLATE_MAX)
            return out

        for orig, trans in zip(batch, lines):
            zh = trans.strip() or orig
            out[orig] = zh
            _cache_set(_translate_cache, orig, zh, _TRANSLATE_MAX)
        return out

    # 并发翻译各批次：原先串行执行，20 条新闻冷启动可累计 30 秒以上
    with ThreadPoolExecutor(max_workers=min(4, len(batches))) as pool:
        for partial in pool.map(_run_batch, batches):
            result.update(partial)

    return result


# ============================================================
# 新闻列表
# ============================================================
def _decode_cursor(cursor):
    """AniNews 游标是 base64(JSON)，解析出 offset；失败返回 None"""
    try:
        padded = cursor + '=' * (-len(cursor) % 4)
        return json.loads(base64.b64decode(padded).decode('utf-8')).get('offset')
    except Exception:
        return None


def _encode_cursor(offset):
    """生成与上游格式兼容的 base64(JSON) 游标"""
    payload = json.dumps({'offset': int(offset)}, separators=(',', ':'))
    return base64.b64encode(payload.encode('utf-8')).decode('utf-8')


# 搜索语料：预取一次全量新闻（当前约百条）并只翻译标题，6 小时内供列表与搜索复用
_CORPUS_KEY = 'corpus_search'
# 最后一次成功的语料单独留一份：上游抖动时用它兜底，绝不被空结果覆盖
_CORPUS_STALE_KEY = 'corpus_search_lastgood'
_CORPUS_BACKOFF_KEY = 'corpus_search_backoff'
_CORPUS_TTL = 21600
# 上游（news.nanyi-anime-club.top）实测极不稳定：读超时 / ConnectionReset(10054)
# 频发。失败后退避重试，避免每个请求都去撞一次 12 秒超时。
_CORPUS_RETRY_BASE = 60
_CORPUS_RETRY_MAX = 600
_corpus_fail_streak = 0


class _CorpusUnavailable:
    """语料「不可用」的显式标记（区别于「成功但确实没有数据」）。

    `[]` 同时代表「上游说没有新闻」和「上游挂了」，调用方无法区分。
    失败路径统一返回本单例，调用方用 `is` 判断即可走各自的兜底分支，
    不会再出现「一个空列表掩盖了上游异常」的情况。
    """

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    # ★ 迭代协议兼容：本单例是为了让「失败」与「确实没有数据」可区分而引入的，
    #   但历史上所有调用方都按「返回 list」的契约写（for / 切片 / len）。
    #   漏改一处调用方就会 TypeError → 详情页 500（t5-audit 实测复现过
    #   find_article_meta 这一处）。这里把单例伪装成空序列，使任何「按列表消费」
    #   的旧调用方自动安全降级为空语料，而不是崩溃。
    def __iter__(self):
        return iter(())

    def __len__(self):
        return 0

    def __bool__(self):
        return False

    def __getitem__(self, index):
        # 切片与索引都按空序列处理：绝不抛 TypeError 让上层 500
        if isinstance(index, slice):
            return []
        raise IndexError(index)

    def __repr__(self):
        return '<CORPUS_UNAVAILABLE>'


_CORPUS_UNAVAILABLE = _CorpusUnavailable()


def _corpus_backoff_active(now=None):
    """上游语料是否处于退避窗口内（用于避免紧接着再打一次上游白等超时）"""
    now = now or datetime.now()
    until = _cache_get(_cache, _CORPUS_BACKOFF_KEY)
    return bool(until and now < until)


def _corpus_fail(now, reason):
    """所有语料失败路径的唯一出口（异常 / JSON 解析失败 / 结构异常 都要走这里）。

    统一做三件事：
      1) 进入指数退避（60s→600s），避免上游抖动时每个请求都白等一次 12s 超时；
      2) 优先复用 last-known-good 语料（stale-while-error），新闻页/搜索不会变空；
      3) 只有从未成功抓到过语料时，才返回 _CORPUS_UNAVAILABLE 让调用方兜底。
    原实现的缺陷：`if not isinstance(arts, list): return []` 这条早返回既不用
    stale 也不进退避，于是「上游 HTTP 200 但结构异常」时列表会变空、
    且每个请求都重打上游。
    """
    global _corpus_fail_streak
    print(f'>>> 搜索语料不可用（{reason}）')
    delay = min(_CORPUS_RETRY_MAX, _CORPUS_RETRY_BASE * (2 ** _corpus_fail_streak))
    _corpus_fail_streak += 1
    _cache_set(_cache, _CORPUS_BACKOFF_KEY, now + timedelta(seconds=delay), _CACHE_MAX)

    stale = _cache_get(_cache, _CORPUS_STALE_KEY)
    if stale:
        print(f'>>> 复用上次成功语料（{len(stale)} 条），退避 {delay}s')
        return stale
    return _CORPUS_UNAVAILABLE


def _get_search_corpus():
    """获取搜索语料（标题已译）。

    返回：list（成功，可能为空=上游确实没有新闻）
          _CORPUS_UNAVAILABLE（失败且无历史语料可复用）
    """
    global _corpus_fail_streak
    now = datetime.now()
    entry = _cache_get(_cache, _CORPUS_KEY)
    if entry:
        arts, expires = entry
        if now < expires:
            if arts:
                # 顺带回填已缓存的摘要翻译（键是原文，需持锁快照后读）
                cached_excerpts = _cache_items(_translate_cache)
                excerpt_map = {k: v for k, v in cached_excerpts if isinstance(k, str)}
                for a in arts:
                    e = a.get('excerpt') or ''
                    if e and not a.get('excerpt_zh') and excerpt_map.get(e):
                        a['excerpt_zh'] = excerpt_map[e]
            return arts

    # 退避窗口内不再打上游：复用历史语料，没有历史才报不可用。
    if _corpus_backoff_active(now):
        stale = _cache_get(_cache, _CORPUS_STALE_KEY)
        if stale:
            return stale
        return _CORPUS_UNAVAILABLE

    try:
        res = requests.get(f'{ANI_NEWS_API}/api/news', params={'limit': 100}, timeout=12)
        data = res.json()
    except Exception as e:
        # 网络异常 / JSON 解析失败
        return _corpus_fail(now, f'{type(e).__name__}: {e}')

    arts = data.get('data') if isinstance(data, dict) else None
    if not isinstance(arts, list):
        # ★ 结构异常（例如上游返回 {"error":"maintenance"}）也必须走统一失败出口：
        #   原先这里直接 return []，导致列表变空且退避不生效。
        return _corpus_fail(now, f'响应结构异常: {type(data).__name__}')

    arts = [a for a in arts if isinstance(a, dict)]

    # 只批量翻译标题：摘要不额外翻译，控制冷启动耗时
    titles = [a.get('title') for a in arts if a.get('title')]
    if titles:
        try:
            zh = _translate_batch(titles)
            for a in arts:
                if a.get('title'):
                    a['title_zh'] = zh.get(a['title'], a['title'])
        except Exception as e:
            # 翻译失败不算语料失败：语料本身可用，降级为原文标题
            print(f'>>> 语料标题翻译失败（降级用原文）: {e}')

    if arts:
        _cache_set(_cache, _CORPUS_KEY, (arts, now + timedelta(seconds=_CORPUS_TTL)), _CACHE_MAX)
        _cache_set(_cache, _CORPUS_STALE_KEY, arts, _CACHE_MAX)
        _cache_pop(_cache, _CORPUS_BACKOFF_KEY)
        _corpus_fail_streak = 0
    # 注意：arts 为空时**不进失败出口**——上游正常响应「确实没有新闻」不同于上游故障，
    # 不应触发退避，也不应污染 last-known-good。
    return arts


def _ensure_translated(articles, translate_excerpt=True):
    """统一翻译补充：保证返回的每条都有 title_zh / excerpt_zh（有原文时）。

    ★ 为什么要有这个函数：站点有**三条**取新闻的路径（直连上游 / 本地语料切片 /
    搜索），历史上翻译覆盖不一致 —— 直连路径翻标题+摘要，语料切片与搜索只翻标题，
    于是用户看到「第一页有翻译，翻页和搜索没翻译」。把「翻译补充」收敛到唯一入口，
    以后新增路径只要调用它，就不会再漏翻译。

    性能约定：
      - 只翻译**传入的这一页**（通常 ≤20 条），绝不翻译全量语料；
      - 完全复用 _translate_cache（键为原文），已译过的不会重复计费；
      - 标题与摘要一次批量提交，减少往返。
    """
    if not articles:
        return articles

    pending = []
    for a in articles:
        if not isinstance(a, dict):
            continue
        title = a.get('title')
        excerpt = a.get('excerpt')
        if title and not a.get('title_zh'):
            pending.append(title)
        if translate_excerpt and excerpt and not a.get('excerpt_zh'):
            pending.append(excerpt)

    if pending:
        try:
            zh = _translate_batch(pending)
        except Exception as e:          # 翻译失败不能让整个列表接口失败
            print(f'>>> 列表翻译补充失败（降级用原文）: {e}')
            zh = {}

        for a in articles:
            if not isinstance(a, dict):
                continue
            title = a.get('title')
            excerpt = a.get('excerpt')
            if title:
                a['title_zh'] = a.get('title_zh') or zh.get(title, title)
            if translate_excerpt and excerpt:
                a['excerpt_zh'] = a.get('excerpt_zh') or zh.get(excerpt, excerpt)

    return articles


def find_article_meta(url):
    """按链接查找文章元数据：先查搜索语料，再回退已缓存列表；找不到返回 None"""
    if not url:
        return None
    # 显式判哨兵（双保险）：单例本身已实现空序列协议，这里再判一次让意图明确，
    # 也避免将来有人给单例加了成员导致语义漂移。
    corpus = _get_search_corpus()
    if corpus is _CORPUS_UNAVAILABLE:
        corpus = []
    for a in corpus:
        if (a.get('link') or a.get('url')) == url:
            return a
    cache_items = [(k, v) for k, v in _cache_items(_cache) if str(k).startswith('news_')]
    if cache_items:
        latest_key = max(cache_items, key=lambda kv: kv[1][1])[0]
        cached_articles = _cache_get(_cache, latest_key)[0]
        arts = cached_articles[0] if isinstance(cached_articles, tuple) else cached_articles
        for a in arts:
            if (a.get('link') or a.get('url')) == url:
                return a
    return None


def get_anime_news(limit=20, tag=None, cursor=None):
    """获取动漫新闻列表，返回 (articles, meta)"""
    cache_key = f'news_{limit}_{tag or "all"}_{cursor or "start"}'
    now = datetime.now()

    entry = _cache_get(_cache, cache_key)
    if entry:
        data, expires = entry
        if now < expires:
            return data

    # 无 tag 且游标可解析时，直接从本地语料切片返回：
    # 避免冷启动串行等待上游抓取 + 逐批翻译
    if not tag:
        offset = _decode_cursor(cursor) if cursor else 0
        if offset is not None and offset >= 0:
            corpus = _get_search_corpus()
            # 显式区分「上游确实没有新闻」（[]）与「语料不可用」（哨兵对象）
            if isinstance(corpus, list) and corpus:
                page = corpus[offset:offset + limit]
                # ★ 懒翻译：语料只预翻了标题，这里按需补齐**当前页**的标题+摘要。
                #   只翻这一页（≤20 条）并复用 _translate_cache，绝不翻全量 100 条，
                #   否则冷启动会被翻译拖垮（这正是上一版「只翻标题」的取舍来源）。
                page = _ensure_translated(page)
                has_more = offset + limit < len(corpus)
                next_cursor = _encode_cursor(offset + limit) if has_more else None
                result = (page, {'total': len(corpus), 'hasMore': has_more,
                                 'nextCursor': next_cursor})
                _cache_set(_cache, cache_key, (result, now + timedelta(seconds=_CACHE_TTL)), _CACHE_MAX)
                return result

    params = {'limit': limit}
    if tag:
        params['tag'] = tag
    if cursor:
        params['cursor'] = cursor

    url = f'{ANI_NEWS_API}/api/news'
    print(f'>>> 请求 AniNews: {url} params={params}')

    try:
        res = requests.get(url, params=params, timeout=15)
        print(f'>>> HTTP 状态: {res.status_code}')

        data = res.json()

        articles = []
        meta = {}
        if isinstance(data, dict):
            articles = data.get('data') or data.get('articles') or []
            meta = data.get('meta') or {}
        elif isinstance(data, list):
            articles = data

        # ★ 走统一的翻译补充入口（标题 + 摘要），与语料切片/搜索路径保持一致
        articles = _ensure_translated(articles)

        result = (articles, meta)
        _cache_set(_cache, cache_key, (result, now + timedelta(seconds=_CACHE_TTL)), _CACHE_MAX)
        return result

    except Exception as e:
        print(f'>>> AniNews 请求失败: {e}')
        traceback.print_exc()
        return [], {}


# ============================================================
# 搜索
# ============================================================
def search_news(keyword, limit=50):
    """搜索新闻。中文关键词会先翻译成英文再匹配。"""
    if not keyword:
        return []

    keyword = keyword.strip()
    if not keyword:
        return []

    # 搜索缓存键
    cache_key = f'search:{keyword.lower()}:{limit}'
    now = datetime.now()
    entry = _cache_get(_cache, cache_key)
    if entry:
        cached_data, expires = entry
        if now < expires:
            return cached_data

    has_chinese = any('\u4e00' <= c <= '\u9fff' for c in keyword)

    # 语料拉取与关键词翻译并行执行：原实现三段串行
    # （列表抓取→批量翻译→关键词翻译），冷启动最坏可累计 30 秒以上
    kw_future = None
    with ThreadPoolExecutor(max_workers=2) as pool:
        corpus_future = pool.submit(_get_search_corpus)
        if has_chinese:
            kw_future = pool.submit(_translate_keyword, keyword)
        articles = corpus_future.result()

    # 语料不可用（哨兵）或确实为空时，回退已缓存的列表数据。
    # 注意必须用 isinstance 判断：哨兵对象是「真值」，直接遍历会遍历到对象本身。
    if not isinstance(articles, list):
        articles = []
    if not articles:
        cache_items = [(k, v) for k, v in _cache_items(_cache) if str(k).startswith('news_')]
        if cache_items:
            latest_key = max(cache_items, key=lambda kv: kv[1][1])[0]  # 按过期时间排序
            cached_articles = _cache_get(_cache, latest_key)[0]
            if isinstance(cached_articles, tuple):
                articles = cached_articles[0]  # 格式：(articles, meta)
            else:
                articles = cached_articles

    keywords = [keyword.lower()]

    # 回退后仍可能不是 list（极端情况），统一保证可迭代
    if not isinstance(articles, list):
        articles = []

    # 关键词翻译已在语料拉取时并行执行，这里只取结果
    if kw_future is not None:
        try:
            translated = kw_future.result()
            if translated and translated.lower() != keyword.lower():
                keywords.append(translated.lower())
                print(f'>>> 搜索词翻译: "{keyword}" → "{translated}"')
        except Exception as e:
            print(f'>>> 搜索词翻译失败: {e}')
            # 翻译失败时仅使用原关键词搜索

    results = []
    for a in articles:
        title = (a.get('title') or '').lower()
        title_zh = (a.get('title_zh') or '').lower()
        excerpt = (a.get('excerpt') or '').lower()
        excerpt_zh = (a.get('excerpt_zh') or '').lower()
        tags = ' '.join(a.get('tags') or []).lower()

        haystack = ' '.join([title, title_zh, excerpt, excerpt_zh, tags])
        if any(kw in haystack for kw in keywords):
            results.append(a)
            if len(results) >= limit:
                break

    # 不缓存空结果：避免首次搜索无数据后，后续相同关键词也永远返回空
    if not results:
        return []

    # ★ 搜索结果同样补齐翻译（标题+摘要）：否则用户看到「搜索出来的新闻没翻译」
    results = _ensure_translated(results)

    # 缓存搜索结果 (5分钟)
    _cache_set(_cache, cache_key, (results, now + timedelta(minutes=5)), _CACHE_MAX)
    
    return results


def _translate_keyword(keyword):
    """把搜索关键词翻译成英文"""
    cache_key = 'KW:' + keyword
    cached = _cache_get(_translate_cache, cache_key)
    if cached:
        return cached

    translated = _aliyun_call(keyword, source='zh', target='en')
    if translated:
        translated = translated.strip().strip('"\'')
        _cache_set(_translate_cache, cache_key, translated, _TRANSLATE_MAX)
        return translated
    return keyword


# ============================================================
# 视频提取（仅「嵌入 / 外链播放」）
# ============================================================
# ★ 能力边界（务必不要越过）：
#   本站只做「视频嵌入 / 外链播放」——把原文页里已有的白名单播放器用 <iframe>
#   嵌进详情页，或给一个跳转到原站的外链。**绝不做**以下任何一件事：
#     1. 服务端下载视频文件；
#     2. 服务端转码 / 切片 / 生成 HLS；
#     3. 把视频转存到 Supabase Storage。
#   原因：Vercel 免费计划 Serverless 无常驻磁盘、maxDuration=30s，一集动画动辄
#   数百 MB，下载/转码必然超时并吃掉免费额度；Supabase 免费计划 1GB Storage
#   也远不够；此外转存第三方版权视频存在版权风险。
#   视频本体永远由 YouTube / Bilibili / Vimeo 的播放器或原站自己提供。
#
#   同理，视频 URL **不走** 自家的 /api/anime/news-image 代理（那是给图片用的，
#   只放行 content-type: image/*，且 8s 超时）——视频必须直连上游，否则必撞
#   30s 超时并放大流量成本。

# 允许 <iframe> 嵌入的播放器域名白名单（精确匹配 host，或匹配其子域）
_VIDEO_EMBED_HOSTS = (
    'youtube.com', 'youtube-nocookie.com', 'youtu.be',
    'bilibili.com', 'b23.tv',
    'vimeo.com',
    'dailymotion.com', 'dai.ly',
    'nicovideo.jp',
    'streamable.com',
    'twitch.tv',
    'facebook.com', 'fb.watch',
)

# 明确拒绝的伪协议 / 本地协议（javascript:、data: 等一律不得进入结果）
_BAD_URL_SCHEMES = ('javascript', 'data', 'vbscript', 'file', 'blob', 'about', 'jar')

# 只有在 <iframe> 的 src 里出现这些「播放器路径特征」时才认为是视频嵌入，
# 否则视频新闻页里那些自家文章嵌入、邮件订阅嵌入都会被误判成视频。
_VIDEO_EMBED_PATH_HINTS = (
    '/embed', '/player', '/video', '/v/', 'yt_embed', '/plugins/video',
)

# <video>/<source>/og:video 这类直链只接受真正的视频文件扩展名
_VIDEO_FILE_EXT = ('.mp4', '.webm', '.ogv', '.ogg', '.m4v', '.mov', '.m3u8', '.mpd')

# 返回结果里 videos 元素的 type 取值：iframe / video / meta


def _safe_media_url(raw, base=None):
    """把原始 src/content 规范化成可安全返回的绝对 https URL；不合法返回 None。

    这是所有视频/媒体 URL 的唯一入口，负责挡住：
      - javascript: / data: / vbscript: / file: / blob: 等伪协议（含大小写混淆、
        前导空白与控制字符混淆，如 "\\tjava\\nscript:"）；
      - 协议相对地址 //evil.com/x（直接拒绝，不尝试补全协议：补全后仍会在
        https 页面下发出跨域请求，白名单校验必须看到确定无疑的 host）；
      - 内嵌引号 / 尖括号 / 反引号 / 空白的属性逃逸载荷（" onload="alert(1)）；
      - **HTML 实体残留**：BeautifulSoup 会把 `&amp;quot;` 解码成 `&quot;`，
        于是 URL 里会残留 `&quot;` 文本；若只做字符串前缀校验，攻击者可用
        `https://www.youtube.com/embed/a&amp;quot;onload=&amp;quot;alert(1)`
        构造出「域名合法但 URL 内容不受控」的值。这里先 html.unescape 再校验，
        解出引号后立即整条拒绝。
    畸形或不可信的 URL 一律 **整条丢弃**（返回 None）：既不降级成 embed，
    也不退化成外链——URL 本身不可信时，任何形式输出给前端都是隐患。
    正常 URL（含 `?si=xxx&t=30` 这类查询串，`&` 合法）不会被误杀。
    """
    if not raw:
        return None
    if not isinstance(raw, str):
        return None
    # 反复 unescape：`&amp;amp;quot;` 这类多重编码也要解到位，直到稳定为止
    cleaned = raw.strip()
    for _ in range(3):
        unescaped = html.unescape(cleaned)
        if unescaped == cleaned:
            break
        cleaned = unescaped.strip()
    if not cleaned:
        return None
    # 解出实体后，任何引号/尖括号/反引号/空白/控制字符都说明 URL 不可信
    if re.search(r'[\s\x00-\x1f\x7f"\'`<>\\]', cleaned):
        return None
    if cleaned[0] in '<>"\'`':
        return None
    # 协议相对与反斜杠形式一律拒绝（不补全协议）
    if cleaned.startswith('//') or cleaned.startswith('\\\\'):
        return None
    if ':' in cleaned.split('/', 1)[0].split('?', 1)[0]:
        scheme = cleaned.split(':', 1)[0].lower()
        if scheme in _BAD_URL_SCHEMES:
            return None
        if scheme not in ('http', 'https'):
            return None
    try:
        abs_url = urljoin(base, cleaned) if base else cleaned
        parsed = urlsplit(abs_url)
    except (ValueError, AttributeError):
        return None
    if parsed.scheme.lower() not in ('http', 'https'):
        return None
    if not _strict_netloc_ok(parsed):
        return None
    # 片段（#...）一律拒绝：合法视频嵌入/直链都不需要片段，而
    # `https://www.youtube.com#@evil.example.com` 这类「netloc 干净、脏东西
    # 藏在 fragment 里」的地址正是解析器混淆的常见形态——不同解析器对
    # `#` 与 `@` 的先后处理不一致。整条丢弃，绝不输出。
    if parsed.fragment:
        return None
    # 统一升级到 https：站点自身是 https，避免混合内容被浏览器拦截
    if parsed.scheme.lower() != 'https':
        abs_url = 'https://' + abs_url.split('://', 1)[1]
    return abs_url


def _strict_netloc_ok(parsed):
    """netloc 严格校验，挡住 URL 解析器差异（parser confusion）攻击。

    形如 `https://www.youtube.com:evil.example.com/embed/x` 的地址，
    urlsplit().hostname 会「友好地」返回 www.youtube.com（于是域名白名单
    轻松通过），但 netloc 里其实塞了额外内容；浏览器 / 代理 / requests
    对它的解释可能不一致，属于典型的解析器混淆漏洞。
    这里要求 netloc 必须**恰好**等于 hostname（可带合法数字端口），
    任何多余字符（伪端口、userinfo、@、残留内容）一律拒绝。
    """
    try:
        port = parsed.port  # 非法端口（如 evil.example.com）会抛 ValueError
    except ValueError:
        return False
    if parsed.username or parsed.password or '@' in parsed.netloc:
        return False
    host = parsed.hostname
    if not host:
        return False
    port_part = f':{port}' if port else ''
    # host 已被 urlsplit 小写化，故 netloc 也按小写比对（大写域名应正常放行）
    return parsed.netloc.lower() == host + port_part


def _host_in_allowlist(host, allowlist):
    """host 是否精确等于白名单域，或是其子域（挡掉 youtube.com.evil.test 这类）"""
    if not host:
        return False
    host = host.lower().strip('.')
    for allowed in allowlist:
        if host == allowed or host.endswith('.' + allowed):
            return True
    return False


def _is_allowed_embed_url(url):
    """该 URL 是否允许作为 <iframe> 嵌进本站页面"""
    try:
        parsed = urlsplit(url)
    except (ValueError, AttributeError):
        return False
    if parsed.scheme.lower() != 'https':
        return False
    # 同样走 netloc 严格校验，防止畸形 netloc 在此二次校验时蒙混过关
    if not _strict_netloc_ok(parsed):
        return False
    # host 精确匹配白名单域或其子域（绝不用子串判断）
    return _host_in_allowlist(parsed.hostname, _VIDEO_EMBED_HOSTS)


def _is_direct_video_file(url):
    """是否为受支持的视频直链（按扩展名判断，m3u8/HLS 也算）"""
    try:
        path = urlsplit(url).path.lower()
    except (ValueError, AttributeError):
        return False
    return path.endswith(_VIDEO_FILE_EXT)


def _looks_like_video_embed(url):
    """iframe 是否像播放器嵌入：白名单域 + 播放器路径特征，或 youtu.be 短链"""
    if not _is_allowed_embed_url(url):
        return False
    parsed = urlsplit(url)
    host = (parsed.hostname or '').lower()
    if host in ('youtu.be', 'b23.tv', 'dai.ly'):
        return True
    if _host_in_allowlist(host, ('youtube.com', 'youtube-nocookie.com', 'vimeo.com',
                                 'dailymotion.com', 'twitch.tv', 'facebook.com')):
        # 这些站的播放器路径特征：/embed/、/video/、/player/、/v/
        return any(hint in parsed.path.lower() for hint in _VIDEO_EMBED_PATH_HINTS)
    if _host_in_allowlist(host, ('bilibili.com',)):
        # B 站：/player.html、/blackboard/...、BV 视频页都视作可嵌入
        return ('/player' in parsed.path.lower()
                or '/blackboard' in parsed.path.lower()
                or bool(re.match(r'^/video/', parsed.path, re.I)))
    if _host_in_allowlist(host, ('nicovideo.jp', 'streamable.com')):
        return True
    return False


def _youtube_id_from_handler_url(url):
    """从 Anime Corner 的 youtube-iframe-handler.html?yt_embed=XXXX 里取出视频 ID"""
    try:
        parsed = urlsplit(url)
    except (ValueError, AttributeError):
        return None
    query = parse_qs(parsed.query)
    for key in ('yt_embed', 'yt', 'v', 'video_id'):
        values = query.get(key)
        if values and re.fullmatch(r'[A-Za-z0-9_-]{6,20}', values[0] or ''):
            return values[0]
    return None


def _dedupe_videos(videos, limit=6):
    """按 url 去重并限制条数（Vercel 免费计划：详情页不该无限膨胀）"""
    out = []
    seen = set()
    for v in videos:
        url = v.get('url')
        if not url or url in seen:
            continue
        seen.add(url)
        out.append(v)
        if len(out) >= limit:
            break
    return out


def _extract_videos(soup, base_url, title):
    """从原文页提取视频嵌入/外链，返回可直接交给模板的 dict 列表。

    只返回两类：
      - {'type': 'iframe', 'url': ..., 'embed': True, 'label': ...}
        白名单播放器，模板可安全 <iframe> 嵌入；
      - {'type': 'video'|'meta', 'url': ..., 'embed': False, 'label': ...}
        视频直链或 og:video 等，模板只能渲染成「新窗口打开」的外链。

    注意：本函数是纯解析，不会为了拿视频 info 去请求任何第三方地址
    （不做 oEmbed 解析），因此不存在由原文 HTML 触发的任意域 SSRF。
    """
    videos = []

    # 地区分流（Lead 决策 2026-10-17）：用户反馈「油管要翻墙，视频播不出来」。
    # cn 域（bilibili/b23.tv/acfun）国内可直连，模板可自动嵌 iframe；
    # overseas 域（youtube/vimeo 等）国内被墙，自动嵌只会得到一个纯黑空框，
    # 必须默认不嵌、给明确提示 + 点击后才注入 iframe。
    cn_hosts = ('bilibili.com', 'b23.tv', 'acfun.cn')

    def _region_of(u):
        h = (urlsplit(u).hostname or '').lower()
        return 'cn' if h == 'bilibili.com' or h.endswith('.bilibili.com') \
            or h in cn_hosts or any(h == d or h.endswith('.' + d) for d in cn_hosts) \
            else 'overseas'

    # 1) <iframe src>：白名单播放器
    for tag in soup.find_all('iframe'):
        raw = tag.get('src') or tag.get('data-src') or ''
        abs_url = _safe_media_url(raw, base_url)
        if not abs_url:
            continue
        yt_id = _youtube_id_from_handler_url(abs_url)
        if yt_id:
            # 视频站自家的「YouTube iframe handler」页（如 Anime Corner 的
            # /acwp-direct/youtube-iframe-handler.html?yt_embed=XXXX）跑在**原文站
            # 自己的域名**上，无法在站外嵌入（X-Frame-Options 会拒绝），因此只取
            # 其中的 yt_embed 视频 ID，换成标准 YouTube 播放器地址再嵌入。
            # 注意此处**只信 URL 结构，不发起任何请求**（无 oEmbed、无 SSRF）。
            videos.append({'type': 'iframe', 'url':
                           f'https://www.youtube.com/embed/{yt_id}',
                           'embed': True, 'label': 'YouTube',
                           'region': 'overseas'})
            continue
        if _looks_like_video_embed(abs_url):
            host = (urlsplit(abs_url).hostname or '').lower()
            label = 'Bilibili' if 'bilibili' in host else (
                'Vimeo' if 'vimeo' in host else 'YouTube' if 'youtu' in host else host)
            videos.append({'type': 'iframe', 'url': abs_url,
                           'embed': True, 'label': label,
                           'region': _region_of(abs_url)})

    # 2) <video src> / <source src>：视频直链，只能外链播放
    for tag in soup.find_all(['video', 'source']):
        raw = tag.get('src') or ''
        abs_url = _safe_media_url(raw, base_url)
        if not abs_url:
            continue
        if _is_allowed_embed_url(abs_url) and _looks_like_video_embed(abs_url):
            videos.append({'type': 'iframe', 'url': abs_url,
                           'embed': True, 'label': '视频播放器',
                           'region': _region_of(abs_url)})
        elif _is_direct_video_file(abs_url):
            videos.append({'type': 'video', 'url': abs_url,
                           'embed': False, 'label': '视频文件',
                           'region': _region_of(abs_url)})

    # 3) og:video / og:video:url / og:video:secure_url / twitter:player meta
    for meta in soup.find_all('meta'):
        prop = (meta.get('property') or meta.get('name') or '').strip().lower()
        if not (prop.startswith('og:video') or prop.startswith('twitter:player')):
            continue
        abs_url = _safe_media_url(meta.get('content') or '', base_url)
        if not abs_url:
            continue
        if _is_allowed_embed_url(abs_url) and _looks_like_video_embed(abs_url):
            videos.append({'type': 'iframe', 'url': abs_url,
                           'embed': True, 'label': '视频播放器',
                           'region': _region_of(abs_url)})
        else:
            # 任意域的 og:video 允许作为「外链」返回（如原站自托管 mp4），
            # 但 embed=False，模板绝不能把它渲染成 iframe。
            videos.append({'type': 'meta', 'url': abs_url,
                           'embed': False, 'label': title or '原站视频',
                           'region': _region_of(abs_url)})

    return _dedupe_videos(videos)


# ============================================================
# 新闻正文
# ============================================================
def get_article_content(url, fail_reason=None):
    """抓取原文章正文并翻译成中文

    返回 dict，成功时新增两个**供前端做状态反馈**的字段（向后兼容，旧调用方
    只读 title/content/images/videos 不受影响）：
      - 'status': 'ok' | 'translated_failed' —— 正文拿到了，但翻译降级成了原文
      - 'reason': 失败/降级的人类可读原因（用于详情页提示，不含内部异常原文）
    返回 None 表示**抓取完全失败**（网络/被拦/正文过短），调用方应给出重试入口。

    fail_reason：可选 list，失败时回填一条**用户可读**的原因分类
    （'网络超时' / '连接被中断' / '原站拒绝访问' / '正文过短' / '解析失败'），
    供视图层原样传给模板做提示——但绝不把内部异常文本透给用户。
    """
    def _bail(reason):
        if fail_reason is not None:
            fail_reason.append(reason)
        return None

    if not url:
        return _bail('链接无效')

    now = datetime.now()
    cached_article = _cache_get(_article_cache, url)
    if cached_article:
        data, expires = cached_article
        if now < expires:
            return data

    try:
        # 真实浏览器 UA + Accept-Language：部分站点（MyAnimeList 等）对
        # 非浏览器 UA 直接 403/断连，这是「部分正文抓取失败」的一个诱因。
        res = safe_get(url, headers={
            'User-Agent': _UA_BROWSER,
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9,zh-CN;q=0.8',
        }, timeout=_ARTICLE_FETCH_TIMEOUT)
        if res is None:
            # safe_get 返回 None：网络中断 / 非公网地址 / 重定向过多。
            # 实测本环境对 news.google.com 跳转与部分 CDN 会频繁断连，
            # 这属于**上游传输层不稳定**，不是 SSRF 校验误伤（已取证）。
            print('>>> 抓取原文失败（safe_get 返回 None：网络中断或地址被拦）')
            return _bail('原站连接失败')
        res.raise_for_status()
    except requests.exceptions.Timeout:
        return _bail('网络超时')
    except requests.exceptions.HTTPError as e:
        code = getattr(getattr(e, 'response', None), 'status_code', None)
        return _bail('原站拒绝访问' if code in (401, 403, 429) else '原站返回错误')
    except requests.exceptions.ConnectionError:
        return _bail('连接被中断')
    except Exception as e:
        print(f'>>> 抓取原文失败: {e}')
        return _bail('网络请求失败')

    try:
        soup = BeautifulSoup(res.text, 'html.parser')

        title = soup.title.string.strip() if soup.title and soup.title.string else ''

        # ★ 视频提取必须在 decompose() 之前做：iframe / video / source / meta
        #   下面会被当成噪音标签删掉，先提取才能拿到视频来源。
        #   只做嵌入与外链，提取本身不发起任何网络请求。
        videos = _extract_videos(soup, url, title)

        for tag in soup(['script', 'style', 'nav', 'header', 'footer',
                         'aside', 'form', 'iframe', 'noscript', 'button']):
            tag.decompose()

        article = (
            soup.find('article') or
            soup.find('main') or
            soup.find('div', class_=re.compile(r'(article|content|post|entry)', re.I)) or
            soup.body
        )

        if not article:
            return _bail('无法识别正文区域')

        paragraphs = []
        for p in article.find_all(['p', 'h2', 'h3']):
            text = p.get_text(' ', strip=True)
            if text and len(text) > 20:
                paragraphs.append(text)

        seen = set()
        cleaned = []
        for p in paragraphs:
            if p not in seen:
                seen.add(p)
                cleaned.append(p)

        full_text = '\n\n'.join(cleaned[:40])

        if len(full_text) < 100:
            # 阈值保留（防把导航栏短文本当正文），但要区分「抓到了但太短」
            # 与「压根没抓到」，否则用户无法判断该不该重试。
            return _bail('正文过短')

        # 翻译正文；stats 用于区分「译好」与「翻译失败降级为原文」
        stats = {}
        try:
            translated = _translate_article(full_text, stats=stats)
        except TypeError:
            # 兼容「单参数签名」的旧实现/测试桩，不能因为多了个可选参数就崩
            translated = _translate_article(full_text)
            stats.setdefault('ok', True)
            stats.setdefault('failed', 0)

        # 提取文章中所有 https 图片链接
        images = []
        for img in article.find_all('img'):
            src = img.get('src')
            if not src:
                continue
            # 转为绝对地址
            import urllib.parse
            abs_url = urllib.parse.urljoin(url, src)
            if abs_url.startswith('https://'):
                images.append(abs_url)
        # 去重并限制数量
        seen = set()
        images = [u for u in images if u not in seen and not seen.add(u)][:8]
        
        result = {
            'title': title,
            'content': translated,
            # 原文正文：供「设置 → 语言与翻译」里的「仅原文 / 双语对照」使用。
            # 译者优先（默认）时前端不读它，因此纯增量、向后兼容。
            'content_original': full_text,
            'images': images,
            # 新增字段：向后兼容（旧调用方只读 title/content/images）
            # 'videos' 元素形如 {'type','url','embed','label'}，
            # 仅 embed=True 的白名单项允许被模板渲染成 iframe。
            'videos': videos,
            # 状态反馈（问题 9）：翻译失败时 content 是原文，必须让前端知道
            'status': 'ok' if stats.get('ok', True) else 'translated_failed',
            'reason': '' if stats.get('ok', True) else '翻译服务不可用，当前显示原文',
        }
        _cache_set(_article_cache, url, (result, now + timedelta(seconds=_ARTICLE_TTL)), _ARTICLE_MAX)
        return result

    except Exception as e:
        print(f'>>> 解析原文失败: {e}')
        traceback.print_exc()
        return _bail('正文解析失败')


def _translate_article(text, stats=None):
    """翻译整篇文章。按段落分批调用阿里云，各批次并发执行。

    stats：可选 dict，回填 {'ok': bool, 'failed': int}，供调用方区分
    「翻译成功」与「翻译失败已降级为原文」——用户看到英文时必须能知道原因。
    """
    paragraphs = [p for p in text.split('\n\n') if p.strip()]
    if not paragraphs:
        if stats is not None:
            stats['ok'] = True
            stats['failed'] = 0
        return text

    BATCH_SIZE = 5  # 每批 5 段
    failed_batches = [0]
    failed_lock = threading.Lock()

    def _mark_failed():
        with failed_lock:
            failed_batches[0] += 1

    def _run_batch(batch):
        """翻译一批段落，返回**与输入段数等长**的译文片段列表。

        ★ 段落结构修复：原实现整批拼成一段翻译后返回 [translated]，再用 '\n\n'
        把「批次」连起来，于是同一批内的 5 个段落被压成一段（用户看到正文
        「一大坨」）。现在优先按行还原；行数对不上就逐段翻译，保证段落不粘连。
        """
        joined = '\n'.join(batch)

        # 超过单次限制的批次拆成单段处理
        if len(joined) > _ALIYUN_SINGLE_MAX:
            parts = []
            for p in batch:
                if len(p) > _ALIYUN_SINGLE_MAX:
                    p = p[:_ALIYUN_SINGLE_MAX]
                single = _aliyun_call(p)
                if single:
                    parts.append(single)
                else:
                    _mark_failed()
                    parts.append(p)
            return parts

        translated = _aliyun_call(joined)
        if not translated:
            _mark_failed()
            return list(batch)

        # 行数与段数一致 → 逐段还原（保结构且省调用）
        lines = translated.split('\n')
        if len(lines) == len(batch):
            return [ln.strip() or orig for ln, orig in zip(lines, batch)]

        # 行数不符（阿里云可能吃掉/合并换行）→ 逐段单独翻译，绝不把段落粘连
        parts = []
        for p in batch:
            single = _aliyun_call(p)
            if single:
                parts.append(single)
            else:
                _mark_failed()
                parts.append(p)
        return parts

    jobs = [paragraphs[i:i + BATCH_SIZE]
            for i in range(0, len(paragraphs), BATCH_SIZE)]

    # 各批次并发翻译：原先串行执行，长文每多一批就多等一次阿里云往返
    with ThreadPoolExecutor(max_workers=min(4, len(jobs))) as pool:
        chunk_lists = list(pool.map(_run_batch, jobs))

    if stats is not None:
        stats['failed'] = failed_batches[0]
        stats['ok'] = failed_batches[0] == 0
    return '\n\n'.join(chunk for chunks in chunk_lists for chunk in chunks)
