"""
动漫社官网 · 更新日志模块
====================================
从 GitHub API 拉取全部 commit 记录，供 /history 页面展示网站发展史。
"""
import os
from datetime import datetime, timedelta

import requests

# 内存缓存（24 小时）
_cache = {'all_data': None, 'all_expires': None}
CACHE_TTL_ALL = 86400

# 内部维护类 commit，不展示
EXCLUDE_PREFIXES = (
    'chore:', 'ci:', 'test:', 'docs:', 'style:', 'build:', 'revert:',
    'chore：', 'ci：', 'test：', 'docs：',
    'Merge', 'merge',
)

# 用户关心的 commit
INCLUDE_PREFIXES = (
    'feat:', 'fix:', 'perf:', 'refactor:',
    'feat：', 'fix：', 'perf：', 'refactor：',
    '新增', '修复', '优化', '更新', '添加', '重构',
)

# 纯代码整理类关键词，即使匹配前缀也过滤
EXCLUDE_KEYWORDS = (
    'import', '格式', '命名', '注释', 'docstring', '变量名',
    '缩进', 'lint', 'pylint', '空格', '换行',
)


def should_show(message: str) -> bool:
    """判断 commit message 是否适合展示给用户"""
    msg = message.strip()
    if not msg:
        return False
    if msg.startswith(EXCLUDE_PREFIXES):
        return False
    lower = msg.lower()
    if any(kw in lower for kw in EXCLUDE_KEYWORDS):
        return False
    return True


def _extract_tag(message: str) -> str:
    """从 commit message 中提取类型标签，用于前端上色"""
    # 英文/中文冒号前缀
    for prefix, tag in (
        ('feat:', 'feat'), ('feat：', 'feat'),
        ('fix:', 'fix'), ('fix：', 'fix'),
        ('perf:', 'perf'), ('perf：', 'perf'),
        ('refactor:', 'refactor'), ('refactor：', 'refactor'),
    ):
        if message.startswith(prefix):
            return tag
    # 中文关键词前缀
    for kw, tag in (
        ('新增', 'feat'), ('添加', 'feat'),
        ('修复', 'fix'),
        ('优化', 'perf'),
        ('重构', 'refactor'),
        ('更新', 'feat'),
    ):
        if message.startswith(kw):
            return tag
    return 'other'


def _clean_message(message: str) -> str:
    """去掉前缀，只留正文"""
    for prefix in INCLUDE_PREFIXES:
        if message.startswith(prefix):
            return message[len(prefix):].strip()
    return message


def get_all_commits() -> list:
    """拉取全部有意义的 commit（用于 /history 页面），缓存 24 小时"""
    if _cache['all_data'] and _cache['all_expires'] > datetime.now():
        return _cache['all_data']

    repo = os.getenv('GITHUB_REPO', 'N-zihan/anime-club-website')
    headers = {'Accept': 'application/vnd.github.v3+json'}
    token = os.getenv('GITHUB_TOKEN')
    if token:
        headers['Authorization'] = f'token {token}'

    data = []
    seen_messages = set()
    try:
        page = 1
        MAX_PAGES = 20
        while page <= MAX_PAGES:
            url = f'https://api.github.com/repos/{repo}/commits?per_page=100&page={page}'
            res = requests.get(url, headers=headers, timeout=8)
            if res.status_code != 200:
                print(f'GitHub commits 拉取失败: HTTP {res.status_code}')
                return _cache['all_data'] or []
            batch = res.json()
            if not isinstance(batch, list) or not batch:
                break

            for c in batch:
                first_line = c['commit']['message'].split('\n')[0].strip()
                if not should_show(first_line):
                    continue

                # 去重：相同 message 只保留最早出现的那条
                if first_line in seen_messages:
                    continue
                seen_messages.add(first_line)

                data.append({
                    'message': _clean_message(first_line),
                    'tag': _extract_tag(first_line),
                    'date': c['commit']['author']['date'][:10],
                    'sha': c['sha'][:7],
                    'url': c['html_url'],
                })

            if len(batch) < 100:
                break
            page += 1

        if data:
            _cache['all_data'] = data
            _cache['all_expires'] = datetime.now() + timedelta(seconds=CACHE_TTL_ALL)
        return data
    except Exception:
        # 拉取失败时，返回上一次缓存（如果有），否则空列表
        return _cache['all_data'] or []
