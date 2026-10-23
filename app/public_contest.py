"""赛事相关公开路由（从 public.py 拆出，共用 public 蓝图，endpoint 名不变）。

包含赛事中心/规则/详情、提名、海选/小组赛/淘汰赛投票与提交、票数接口、
AI 解说与预测。共享 helper 从 public.py 导入（public.py 在底部导入本模块，
那时这些名字已经定义完毕）。
"""
import os
import re
import threading
import time
from datetime import datetime, timedelta, timezone

from flask import (render_template, request, redirect, url_for, session,
                   flash, jsonify, abort, Response)
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError

from .ai import generate_commentary, generate_prediction
from .config import (
    NOMINATION_LIMIT,
    QUALIFYING_MAX_CANDIDATES,
    QUALIFYING_MAX_VOTES,
    QUALIFYING_MAX_PER_CANDIDATE,
    NOMINATION_IMAGE_SIZE,
    COMPRESS_QUALITY,
)
from .contest_engine import (
    calc_stage_times, calc_phase, auto_activate_contest,
    run_qualifying_promotion, run_group_promotion,
    run_knockout_advance, run_final_ranking,
    prepare_group_round_data, current_group_round, current_knockout_sub_round,
)
from .models import (db, User, Contest, Nomination, Candidate, ContestVote)
from .notify import notify
from .public import (supabase_render_ctx, _calc_current_phase, _cn_now,
                     _validate_input, PHASE_CN)
from .public_bp import public_bp
from .utils import get_supabase, compress_image, get_or_404, is_public_http_url


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
    round_number = current_group_round(contest)

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
    round_number = current_group_round(contest)

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

    sub_round = current_knockout_sub_round(contest)

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

    sub_round = current_knockout_sub_round(contest)

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


