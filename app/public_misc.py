"""公开页里的独立小路由：离线页 / RSS / 投票实时票数接口。

从 public.py 抽出来（该文件已 2000+ 行），共用同一个 public 蓝图，
endpoint 名保持不变（public.offline / public.anime_news_rss / public.api_contest_vote_stats）。
"""
import os
from datetime import datetime
from urllib.parse import quote
from xml.sax.saxutils import escape

from flask import Response, jsonify, render_template, request
from sqlalchemy import func

from .contest_engine import (calc_stage_times, current_group_round,
                             current_knockout_sub_round)
from .models import Contest, ContestVote, db
from .public_bp import public_bp
from .utils import cn_now, get_or_404


@public_bp.route('/offline')
def offline():
    """PWA 离线兜底页：断网时 service worker 返回它（访客可访问）。"""
    return render_template('offline.html')


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
    now = cn_now()
    times = calc_stage_times(contest.open_at) if contest.open_at else {}

    base = ContestVote.query.filter_by(contest_id=contest.id, gender=gender)
    round_number = None
    sub_round = None

    if mode == 'qualifying':
        base = base.filter_by(round_number=0)
    elif mode == 'group':
        round_number = current_group_round(contest)
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
        sub_round = current_knockout_sub_round(contest)
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


@public_bp.route('/api/cron/weekly-digest', methods=['GET', 'POST'])
def weekly_digest_cron():
    """每周邮件周报（Vercel Cron 每周一 01:00 UTC 调用；用 CRON_SECRET 保护）。"""
    import hmac  # pylint: disable=import-outside-toplevel

    secret = os.getenv('CRON_SECRET')
    if not secret:
        return jsonify({'ok': False, 'error': 'CRON_SECRET 未配置'}), 503

    auth = request.headers.get('Authorization', '')
    token = auth[7:] if auth.startswith('Bearer ') else request.headers.get('X-Cron-Secret', '')
    if not hmac.compare_digest(token, secret):
        return jsonify({'ok': False, 'error': 'forbidden'}), 403

    now = cn_now()
    force = request.args.get('force') == '1'
    if now.weekday() != 0 and not force:
        return jsonify({'ok': True, 'skipped': 'not-monday',
                        'today': now.strftime('%Y-%m-%d'), 'hint': '用 ?force=1 可强制发送'})

    from .digest import send_weekly_digest  # pylint: disable=import-outside-toplevel
    result = send_weekly_digest()
    return jsonify({'ok': True, **result})
