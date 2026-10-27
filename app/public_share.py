"""公开分享落地页：微信/QQ 爬虫抓不到登录墙内的页面，用这些公开页做分享卡片。

- /share              站点总分享页
- /share/contest/<id> 赛事分享页（含可见首图，微信无 JS-SDK 时也能取到缩略图）
"""
from flask import render_template

from .contest_engine import calc_phase, calc_stage_times
from .models import Contest
from .public_bp import public_bp
from .utils import cn_now, get_or_404

PHASE_LABELS = {
    'nomination': '提名期', 'review': '审核期', 'qualifying': '海选投票',
    'qualifying_result': '海选公示',
    'group_round_1': '小组赛第1轮', 'group_round_1_result': '小组赛第1轮公示',
    'group_round_2': '小组赛第2轮', 'group_round_2_result': '小组赛第2轮公示',
    'group_round_3': '小组赛第3轮', 'group_round_3_result': '小组赛第3轮公示',
    'knockout_16': '淘汰赛16强', 'knockout_16_result': '16强公示',
    'knockout_8': '淘汰赛8强', 'knockout_8_result': '8强公示',
    'knockout_4': '淘汰赛4强', 'knockout_4_result': '4强公示',
    'final_vote': '决赛投票', 'final_result': '最终结果', 'closed': '已结束',
}


def _phase_label(contest):
    if not contest.open_at:
        return PHASE_LABELS.get(contest.status, contest.status or '')
    open_at = contest.open_at
    if getattr(open_at, 'tzinfo', None) is not None:
        open_at = open_at.replace(tzinfo=None)
    try:
        phase = calc_phase(contest, cn_now(), calc_stage_times(open_at))
    except Exception:  # pragma: no cover - 阶段计算异常时退回状态
        phase = contest.status
    return PHASE_LABELS.get(phase, phase or contest.status or '')


@public_bp.route('/share')
def share_home():
    """站点总分享页（公开）。"""
    return render_template('share.html')


@public_bp.route('/share/contest/<int:contest_id>')
def share_contest(contest_id):
    """赛事分享页（公开）：访客/爬虫能拿到标题、阶段、候选和首图。

    候选一次取回再在内存里分组/截断：原来 count + 女组 + 男组是三次独立查询，
    再加上 contest 一共 4~5 次（每次都要跨区访问 Supabase），现在固定 2 次。
    """
    contest = get_or_404(Contest, contest_id)
    phase = _phase_label(contest)
    all_candidates = contest.candidates.all()
    total = len(all_candidates)
    female = [c for c in all_candidates if c.gender == 'female'][:4]
    male = [c for c in all_candidates if c.gender == 'male'][:4]
    og_title = f'{contest.title} · {phase}' if phase else contest.title
    og_desc = f'{contest.title} 正在动漫社官网进行（{phase}），共 {total} 位角色参赛，登录即可投票围观～'
    return render_template('share_contest.html', contest=contest, phase=phase,
                           total=total, female=female, male=male,
                           og_title=og_title, og_description=og_desc)
