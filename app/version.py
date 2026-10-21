"""
版本号解析：优先读取 git tag 作为网站版本号
================================================

解析顺序（先命中先返回）：

1. 根目录 `VERSION` 文件
   由 GitHub Actions（推送 tag 时）或发布提交写入。
   部署平台（如 Vercel）的构建包里没有 `.git`（`.vercelignore` 已排除），
   全靠这个文件把 tag 带进运行时。
2. `git describe --tags --abbrev=0`
   本地开发、自建服务器、CI 里存在 `.git` 时，直接读当前可达的 tag。
3. 环境变量 `APP_VERSION`
   仅在前两者都拿不到时作为兜底（原先它是最高优先级，导致线上必须手工维护，
   而 tag 才是版本号的唯一事实来源）。
4. `VERCEL_GIT_COMMIT_SHA` / `GITHUB_SHA` / `GIT_COMMIT`
   退化为 `sha-<前 7 位>`，便于线上排查部署到哪个提交。
5. 最终兜底 `dev`。

结果按进程缓存一次，避免每个请求都读盘或起子进程。
"""

import os
import subprocess
from functools import lru_cache
from pathlib import Path

# app/version.py -> 项目根目录
PROJECT_ROOT = Path(__file__).resolve().parent.parent
VERSION_FILE = PROJECT_ROOT / 'VERSION'

# git 调用超时：拿不到就快速降级，绝不拖慢启动
_GIT_TIMEOUT_SECONDS = 3

_SHA_ENV_KEYS = ('VERCEL_GIT_COMMIT_SHA', 'GITHUB_SHA', 'GIT_COMMIT')


def _version_from_file():
    """读取根目录 VERSION 文件；文件缺失或内容为空时返回 None。"""
    try:
        value = VERSION_FILE.read_text(encoding='utf-8').strip()
    except OSError:
        return None
    return value or None


def _version_from_git_tag():
    """用 git describe 读取最近的可达 tag；无 git、无 .git 或无 tag 时返回 None。"""
    try:
        result = subprocess.run(
            ['git', 'describe', '--tags', '--abbrev=0'],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    tag = result.stdout.strip()
    return tag if result.returncode == 0 and tag else None


def _version_from_env():
    """读取 APP_VERSION 环境变量；未设置或为空时返回 None。"""
    value = (os.getenv('APP_VERSION') or '').strip()
    return value or None


def _version_from_commit():
    """读取部署平台提供的提交 SHA，返回 sha-<前 7 位>；没有则返回 None。"""
    for key in _SHA_ENV_KEYS:
        sha = (os.getenv(key) or '').strip()
        if sha:
            return f'sha-{sha[:7]}'
    return None


@lru_cache(maxsize=1)
def resolve_version():
    """按模块文档中的顺序解析网站版本号（进程内只解析一次）。

    :return: 版本号字符串，形如 v4.17.0 / sha-3066237 / dev
    """
    candidates = (
        _version_from_file(),
        _version_from_git_tag(),
        _version_from_env(),
        _version_from_commit(),
    )
    for candidate in candidates:
        if candidate:
            return candidate
    return 'dev'


def clear_cache():
    """清空解析缓存（测试用：模拟不同环境时强制重新解析）。"""
    resolve_version.cache_clear()
