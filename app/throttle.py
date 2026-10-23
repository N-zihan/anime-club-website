"""跨实例限流：把登录失败计数、验证码发送节流落库。

背景
----
原来的实现在进程内存里（dict），Vercel 是 Serverless 多实例：请求打到不同实例
计数就互相看不见，暴力破解/验证码轰炸可以绕过。这里改成数据库固定窗口计数，
所有实例共享同一份。

语义（与旧实现保持一致）
- throttle_ok(key, seconds)：窗口内第一次返回 True，之后返回 False（不延长窗口）
- login_fail_hit_limit(key)：记录一次失败，返回「是否已超过阈值」
- login_fail_is_limited(key)：只读判断，不计数
- login_fail_reset(key)：登录成功后清零

数据库不可用（无 app context / DB 异常）时回退到进程内实现：宁可少挡一点，
也不能把正常用户锁在门外；同时不抛异常影响主流程。
"""
import logging
import random
import threading
import time
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

_cleanup_lock = threading.Lock()
_last_cleanup = 0.0
CLEANUP_INTERVAL = 600          # 每 10 分钟最多清理一次
ROW_TTL = timedelta(days=1)     # 超过一天的限流行直接删掉

# 内存兜底（无 app context / DB 异常时使用）
_mem_lock = threading.Lock()
_mem_actions = {}


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _has_app_context():
    try:
        from flask import has_app_context  # pylint: disable=import-outside-toplevel
        return has_app_context()
    except Exception:  # pragma: no cover - flask 不可能缺失
        return False


def _maybe_cleanup():
    """概率性清理过期行，避免 rate_limits 表无限增长。"""
    global _last_cleanup
    now = time.monotonic()
    with _cleanup_lock:
        if now - _last_cleanup < CLEANUP_INTERVAL:
            return
        if random.random() > 0.05:
            return
        _last_cleanup = now
    try:
        from .models import db, RateLimit  # pylint: disable=import-outside-toplevel
        cutoff = _now() - ROW_TTL
        RateLimit.query.filter(RateLimit.window_start < cutoff).delete(synchronize_session=False)
        db.session.commit()
    except Exception as exc:  # pragma: no cover - 清理失败不影响功能
        logger.debug('rate limit cleanup skipped: %s', exc)


def _db_hit(key, window_seconds):
    """把 key 在 window_seconds 窗口内的计数 +1，返回新计数。"""
    from .models import db, RateLimit  # pylint: disable=import-outside-toplevel

    now = _now()
    cutoff = now - timedelta(seconds=window_seconds)

    # 1) 窗口内：原子自增
    updated = RateLimit.query.filter(
        RateLimit.key == key, RateLimit.window_start >= cutoff
    ).update({RateLimit.count: RateLimit.count + 1}, synchronize_session=False)
    if updated:
        db.session.commit()
        row = db.session.get(RateLimit, key)
        return int(row.count) if row else 1

    # 2) 已过期：重置计数与窗口起点
    updated = RateLimit.query.filter(
        RateLimit.key == key, RateLimit.window_start < cutoff
    ).update({RateLimit.count: 1, RateLimit.window_start: now}, synchronize_session=False)
    db.session.commit()
    if updated:
        return 1

    # 3) 不存在：插入（并发下可能撞主键，回退成自增）
    try:
        db.session.add(RateLimit(key=key, count=1, window_start=now))
        db.session.commit()
        return 1
    except Exception:
        db.session.rollback()
        RateLimit.query.filter(RateLimit.key == key).update(
            {RateLimit.count: RateLimit.count + 1}, synchronize_session=False)
        db.session.commit()
        row = db.session.get(RateLimit, key)
        return int(row.count) if row else 1


def _db_count(key, window_seconds):
    from .models import db, RateLimit  # pylint: disable=import-outside-toplevel
    cutoff = _now() - timedelta(seconds=window_seconds)
    row = RateLimit.query.filter(
        RateLimit.key == key, RateLimit.window_start >= cutoff
    ).first()
    return int(row.count) if row else 0


def _db_reset(key):
    from .models import db, RateLimit  # pylint: disable=import-outside-toplevel
    RateLimit.query.filter(RateLimit.key == key).delete(synchronize_session=False)
    db.session.commit()


def hit(key, window_seconds):
    """窗口内计数 +1 并返回新计数（供自定义策略使用）。"""
    if not _has_app_context():
        now = time.monotonic()
        with _mem_lock:
            count, start = _mem_actions.get(key, (0, now))
            if now - start > window_seconds:
                count, start = 0, now
            count += 1
            _mem_actions[key] = (count, start)
            return count
    try:
        return _db_hit(key, window_seconds)
    except Exception as exc:
        logger.warning('限流计数回退到内存实现: %s', exc)
        now = time.monotonic()
        with _mem_lock:
            count, start = _mem_actions.get(key, (0, now))
            if now - start > window_seconds:
                count, start = 0, now
            count += 1
            _mem_actions[key] = (count, start)
            return count


def throttle_ok(key, seconds):
    """窗口内第一次放行，之后拒绝（用于验证码发送、找回密码等）。"""
    try:
        return hit(key, seconds) == 1
    finally:
        _maybe_cleanup()


def login_fail_hit_limit(key, max_failures=5, window=60):
    count = hit(key, window)
    return count > max_failures


def login_fail_is_limited(key, max_failures=5, window=60):
    if not _has_app_context():
        now = time.monotonic()
        with _mem_lock:
            entry = _mem_actions.get(key)
        if not entry:
            return False
        count, start = entry
        return (now - start) <= window and count > max_failures
    try:
        return _db_count(key, window) > max_failures
    except Exception as exc:
        logger.warning('限流查询回退到内存实现: %s', exc)
        return False


def login_fail_reset(key):
    if _has_app_context():
        try:
            _db_reset(key)
            return
        except Exception as exc:
            logger.warning('限流清零失败: %s', exc)
    with _mem_lock:
        _mem_actions.pop(key, None)


def reset_all_for_tests():
    """测试专用：清空内存计数（DB 行由测试 fixture 的回滚/重建处理）。"""
    with _mem_lock:
        _mem_actions.clear()
