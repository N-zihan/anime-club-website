"""
动漫社官网 · 萌战系统引擎
====================================

本模块是萌战系统的核心逻辑层，负责所有与赛事相关的计算和状态变更。
它不处理 HTTP 请求，只处理数据和业务逻辑。

核心功能：
1. 阶段计算 —— 根据当前时间和赛事状态判断所处阶段
2. 时间计算 —— 计算所有赛程节点的时间
3. 赛程推进 —— 海选→小组赛→淘汰赛的自动晋级
4. 数据准备 —— 小组赛公示期、淘汰赛对阵等
5. 排名计算 —— 最终排名生成

使用方式：
    from .contest_engine import calc_phase, run_qualifying_promotion, ...

    phase = calc_phase(contest, now, times)
    run_qualifying_promotion(contest)
"""

import random
from datetime import timedelta

from sqlalchemy import func as sa_func
from sqlalchemy.orm.attributes import flag_modified

from .config import STAGE_DAYS, QUALIFYING_TOP_N, GROUP_COUNT
from .models import db, Candidate, ContestVote


# ============================================================
# 时间计算
# ============================================================

def calc_stage_times(open_at):
    """
    根据开始时间计算所有赛程节点时间
    返回一个对象，包含所有阶段的截止时间
    """

    def set_time_to_18(dt):
        result = dt.replace(hour=18, minute=0, second=0, microsecond=0)
        if result < dt:
            result += timedelta(days=1)
        return result

    if not open_at:
        return {
            'nomination_end': None,
            'review_end': None,
            'qualifying_vote_end': None,
            'qualifying_end': None,
            'group_round_1_end': None,
            'group_round_1_result_end': None,
            'group_round_2_end': None,
            'group_round_2_result_end': None,
            'group_round_3_end': None,
            'group_round_3_result_end': None,
            'knockout_16_end': None,
            'knockout_16_result_end': None,
            'knockout_8_end': None,
            'knockout_8_result_end': None,
            'knockout_4_end': None,
            'knockout_4_result_end': None,
            'final_vote_end': None,
            'final_result_end': None,
        }

    # 计算各阶段累计天数
    d = STAGE_DAYS
    nomination_end_days = d['nomination']
    review_end_days = nomination_end_days + d['review']
    qualifying_vote_end_days = review_end_days + d['qualifying_vote']
    qualifying_end_days = qualifying_vote_end_days + d['qualifying_result']
    group_round_1_end_days = qualifying_end_days + d['group_round_1']
    group_round_1_result_end_days = group_round_1_end_days + d['group_round_1_result']
    group_round_2_end_days = group_round_1_result_end_days + d['group_round_2']
    group_round_2_result_end_days = group_round_2_end_days + d['group_round_2_result']
    group_round_3_end_days = group_round_2_result_end_days + d['group_round_3']
    group_round_3_result_end_days = group_round_3_end_days + d['group_round_3_result']
    knockout_16_end_days = group_round_3_result_end_days + d['knockout_16']
    knockout_16_result_end_days = knockout_16_end_days + d['knockout_16_result']
    knockout_8_end_days = knockout_16_result_end_days + d['knockout_8']
    knockout_8_result_end_days = knockout_8_end_days + d['knockout_8_result']
    knockout_4_end_days = knockout_8_result_end_days + d['knockout_4']
    knockout_4_result_end_days = knockout_4_end_days + d['knockout_4_result']
    final_vote_end_days = knockout_4_result_end_days + d['final_vote']
    final_result_end_days = final_vote_end_days + d['final_result']

    return {
        'nomination_end': set_time_to_18(open_at + timedelta(days=nomination_end_days)),
        'review_end': set_time_to_18(open_at + timedelta(days=review_end_days)),
        'qualifying_vote_end': set_time_to_18(open_at + timedelta(days=qualifying_vote_end_days)),
        'qualifying_end': set_time_to_18(open_at + timedelta(days=qualifying_end_days)),
        'group_round_1_end': set_time_to_18(open_at + timedelta(days=group_round_1_end_days)),
        'group_round_1_result_end': set_time_to_18(open_at + timedelta(days=group_round_1_result_end_days)),
        'group_round_2_end': set_time_to_18(open_at + timedelta(days=group_round_2_end_days)),
        'group_round_2_result_end': set_time_to_18(open_at + timedelta(days=group_round_2_result_end_days)),
        'group_round_3_end': set_time_to_18(open_at + timedelta(days=group_round_3_end_days)),
        'group_round_3_result_end': set_time_to_18(open_at + timedelta(days=group_round_3_result_end_days)),
        'knockout_16_end': set_time_to_18(open_at + timedelta(days=knockout_16_end_days)),
        'knockout_16_result_end': set_time_to_18(open_at + timedelta(days=knockout_16_result_end_days)),
        'knockout_8_end': set_time_to_18(open_at + timedelta(days=knockout_8_end_days)),
        'knockout_8_result_end': set_time_to_18(open_at + timedelta(days=knockout_8_result_end_days)),
        'knockout_4_end': set_time_to_18(open_at + timedelta(days=knockout_4_end_days)),
        'knockout_4_result_end': set_time_to_18(open_at + timedelta(days=knockout_4_result_end_days)),
        'final_vote_end': set_time_to_18(open_at + timedelta(days=final_vote_end_days)),
        'final_result_end': set_time_to_18(open_at + timedelta(days=final_result_end_days)),
    }


# ============================================================
# 阶段判断
# ============================================================

def calc_phase(contest, now, times):
    """
    根据赛事状态和当前时间判断所处阶段
    返回 phase 字符串
    """
    if contest.status == 'closed':
        return 'closed'

    if contest.status == 'draft':
        return 'not_started'

    if contest.status == 'group_stage':
        if now <= times['group_round_1_end']:
            return 'group_round_1'
        if now <= times['group_round_1_result_end']:
            return 'group_round_1_result'
        if now <= times['group_round_2_end']:
            return 'group_round_2'
        if now <= times['group_round_2_result_end']:
            return 'group_round_2_result'
        if now <= times['group_round_3_end']:
            return 'group_round_3'
        if now <= times['group_round_3_result_end']:
            return 'group_round_3_result'
        return 'group_round_3_result'

    if contest.status == 'knockout':
        if now <= times['knockout_16_end']:
            return 'knockout_16'
        if now <= times['knockout_16_result_end']:
            return 'knockout_16_result'
        if now <= times['knockout_8_end']:
            return 'knockout_8'
        if now <= times['knockout_8_result_end']:
            return 'knockout_8_result'
        if now <= times['knockout_4_end']:
            return 'knockout_4'
        if now <= times['knockout_4_result_end']:
            return 'knockout_4_result'
        if now <= times['final_vote_end']:
            return 'final_vote'
        if now <= times['final_result_end']:
            return 'final_result'
        if contest.status != 'closed':
            contest.status = 'closed'
            db.session.commit()
        return 'closed'

    open_at = contest.open_at
    if not open_at:
        return 'not_started'

    if now < open_at:
        return 'not_started'
    if now <= times['nomination_end']:
        return 'nomination'
    if now <= times['review_end']:
        return 'review'
    if now <= times['qualifying_vote_end']:
        return 'qualifying'
    if now <= times['qualifying_end']:
        return 'qualifying_result'
    if now <= times['group_round_1_end']:
        return 'group_round_1'
    if now <= times['group_round_1_result_end']:
        return 'group_round_1_result'
    if now <= times['group_round_2_end']:
        return 'group_round_2'
    if now <= times['group_round_2_result_end']:
        return 'group_round_2_result'
    if now <= times['group_round_3_end']:
        return 'group_round_3'
    if now <= times['group_round_3_result_end']:
        return 'group_round_3_result'
    if now <= times['knockout_16_end']:
        return 'knockout_16'
    if now <= times['knockout_16_result_end']:
        return 'knockout_16_result'
    if now <= times['knockout_8_end']:
        return 'knockout_8'
    if now <= times['knockout_8_result_end']:
        return 'knockout_8_result'
    if now <= times['knockout_4_end']:
        return 'knockout_4'
    if now <= times['knockout_4_result_end']:
        return 'knockout_4_result'
    if now <= times['final_vote_end']:
        return 'final_vote'
    if now <= times['final_result_end']:
        return 'final_result'
    if contest.status != 'closed':
        contest.status = 'closed'
        db.session.commit()
    return 'closed'


# ============================================================
# 自动激活
# ============================================================

def auto_activate_contest(contest, now):
    """草稿赛事在到达开始时间后自动激活"""
    if contest.status == 'draft' and contest.open_at:
        open_at = contest.open_at
        if open_at.tzinfo is not None:
            open_at = open_at.replace(tzinfo=None)
        if now.tzinfo is not None:
            now = now.replace(tzinfo=None)
        if now >= open_at:
            contest.status = 'open'
            db.session.commit()
            return True
    return False


# ============================================================
# 海选晋级 → 小组赛
# ============================================================

def run_qualifying_promotion(contest):
    """海选公示结束后，前32名晋级小组赛并随机分组"""
    # 防止并发重复执行：原判据是 female_groups 真值，单性别无候选时
    # generate_groups 返回 []，判据恒假 → 每请求重跑并重排阵。改用独立哨兵，
    # 并保留对既有分组键的兼容判断，避免对线上已生成的赛事重复分组。
    if contest.config and (contest.config.get('qualifying_promoted')
                           or contest.config.get('female_groups')
                           or contest.config.get('male_groups')):
        return

    if contest.config is None:
        contest.config = {}

    female_candidates = contest.candidates.filter_by(gender='female').all()
    male_candidates = contest.candidates.filter_by(gender='male').all()

    def count_votes(candidates):
        # 一次 group_by 取回全部票数：原先逐候选人各发一条 SUM，
        # 候选池数百人时单次晋级就是数百次查询，且被推进循环反复放大。
        ids = [c.id for c in candidates]
        if not ids:
            return []
        vote_totals = dict(
            db.session.query(
                ContestVote.candidate_id,
                sa_func.sum(ContestVote.weight)
            ).filter(
                ContestVote.candidate_id.in_(ids),
                ContestVote.round_number == 0
            ).group_by(ContestVote.candidate_id).all()
        )
        result = [{'candidate': c, 'total_votes': vote_totals.get(c.id, 0)}
                  for c in candidates]
        result.sort(key=lambda x: x['total_votes'], reverse=True)
        return result

    female_result = count_votes(female_candidates)
    male_result = count_votes(male_candidates)

    # 方案 A：禁赛角色无法晋级（过滤掉 banned=True）
    qualified_fem = [item for item in female_result if item['candidate'].banned == False]
    qualified_mal = [item for item in male_result if item['candidate'].banned == False]
    # 同票时必须先按候选人 id 排序再截断：count_votes 用 list.sort 且只按票数排，
    # 同票者的相对顺序取决于数据库返回顺序（Postgres 不保证），导致同一份票数
    # 在不同实例/不同时刻晋级名单不同，32 强边界上的同票角色随机掉出。
    qualified_fem.sort(key=lambda item: (-item['total_votes'], item['candidate'].id))
    qualified_mal.sort(key=lambda item: (-item['total_votes'], item['candidate'].id))
    female_top_32 = [item['candidate'] for item in qualified_fem[:QUALIFYING_TOP_N]]
    male_top_32 = [item['candidate'] for item in qualified_mal[:QUALIFYING_TOP_N]]

    for c in female_top_32:
        c.stage = 'group_stage'
    for c in male_top_32:
        c.stage = 'group_stage'

    def generate_groups(candidates, group_count=GROUP_COUNT):
        """分组。

        名额不足时动态缩减组数（至少 1 组），不再生成空组；
        末组缺员以 None 占位（轮空槽），保持"每组恒 4 槽"不变量——
        下游投票接口、计分、公示数据、模板均按 4 槽索引，
        None 槽由它们现成的判空逻辑安全跳过（不计分，成员自动递补晋级）。
        """
        n = len(candidates)
        if n == 0:
            if group_count == GROUP_COUNT:
                print('>>> [萌战] 该性别无入围角色，本轮无法分组')
            return []
        actual_count = max(1, min(group_count, (n + 3) // 4))
        if n < group_count * 4:
            print(f'>>> [萌战] 该性别入围仅 {n} 人（不足 {group_count * 4}），'
                  f'缩编为 {actual_count} 组，缺额定额以轮空槽补齐')
        random.shuffle(candidates)
        groups = []
        for i in range(actual_count):
            g = candidates[i * 4:(i + 1) * 4]
            if len(g) < 4:
                g = g + [None] * (4 - len(g))
            groups.append(g)
        return groups

    female_groups = generate_groups(female_top_32)
    male_groups = generate_groups(male_top_32)

    # 轮空槽（None）保留写入 config，保持 4 槽不变量
    contest.config['female_groups'] = [[c.id if c is not None else None for c in group] for group in female_groups]
    contest.config['male_groups'] = [[c.id if c is not None else None for c in group] for group in male_groups]
    contest.config['female_top_32'] = [c.id for c in female_top_32]
    contest.config['male_top_32'] = [c.id for c in male_top_32]

    contest.config['female_result'] = [
        {'id': item['candidate'].id,
         'name': item['candidate'].name,
         'source': item['candidate'].source,
         'votes': item['total_votes'],
         'image_url': item['candidate'].image_url}
        for item in female_result[:32]
    ]
    contest.config['male_result'] = [
        {'id': item['candidate'].id,
         'name': item['candidate'].name,
         'source': item['candidate'].source,
         'votes': item['total_votes'],
         'image_url': item['candidate'].image_url}
        for item in male_result[:32]
    ]

    # 推进完成哨兵：两性别入围均为空（分组键为 []）时也落真值标记，
    # 使守卫与 public 的推进条件不再反复触发。
    contest.config['qualifying_promoted'] = True

    flag_modified(contest, 'config')
    db.session.commit()


# ============================================================
# 小组赛晋级 → 淘汰赛
# ============================================================

def run_group_promotion(contest, session=None):
    """小组赛第3轮公示结束后，每组前2晋级淘汰赛，生成16强对阵"""
    # 防止并发重复执行：同海选，空对阵时旧判据恒假会每请求重跑并重排 16 强。
    if contest.config and (contest.config.get('knockout_promoted')
                           or contest.config.get('knockout_matches_female')
                           or contest.config.get('knockout_matches_male')):
        return

    if session is None:
        session = db.session

    if contest.config is None:
        contest.config = {}

    def get_group_stage_results(gender):
        candidates = session.query(Candidate).filter_by(
            contest_id=contest.id,
            gender=gender,
            stage='group_stage'
        ).all()
        if not candidates:
            return [], {}

        groups = contest.config.get(f'{gender}_groups', [])
        if not groups:
            return [], {}

        # ---- 批量获取该性别所有候选人在小组赛 3 轮中的票数 ----
        vote_sums = session.query(
            ContestVote.candidate_id,
            ContestVote.round_number,
            sa_func.sum(ContestVote.weight).label('total_votes')
        ).filter(
            ContestVote.contest_id == contest.id,
            ContestVote.gender == gender,
            ContestVote.round_number.in_([1, 2, 3])
        ).group_by(
            ContestVote.candidate_id,
            ContestVote.round_number
        ).all()

        vote_map = {}
        for row in vote_sums:
            vote_map[(row.candidate_id, row.round_number)] = row.total_votes
        # ------------------------------------------------

        result = {}
        for c in candidates:
            result[c.id] = {
                'name': c.name,
                'candidate': c,
                'wins': 0,
                'draws': 0,
                'losses': 0,
                'points': 0,
                'total_votes': 0,
                'group_index': None
            }

        for gi, group in enumerate(groups):
            for cid in group:
                if cid in result:
                    result[cid]['group_index'] = gi

        for gi, group in enumerate(groups):
            group_candidates = [cid for cid in group if cid in result]
            # 缺员组照常计分（场次级规则见下），至少 2 名成员才有比赛可言
            if len(group_candidates) < 2:
                continue

            for round_num in [1, 2, 3]:
                # 按 4 槽构建对阵（缺额以 None 占位轮空），槽位与 config 中的分组一致
                slots = list(group) + [None] * (4 - len(group))
                if round_num == 1:
                    pairs = [(slots[0], slots[1]), (slots[2], slots[3])]
                elif round_num == 2:
                    pairs = [(slots[0], slots[2]), (slots[1], slots[3])]
                else:
                    pairs = [(slots[0], slots[3]), (slots[1], slots[2])]

                for cid1, cid2 in pairs:
                    # 轮空槽：本场不存在，双方均不计分
                    if (cid1 is None or cid2 is None
                            or cid1 not in result or cid2 not in result):
                        continue

                    b1 = result[cid1]['candidate'].banned
                    b2 = result[cid2]['candidate'].banned
                    if b1 and b2:
                        # 双禁赛：两败俱伤，本场不计分不产生积分
                        continue

                    if b1 or b2:
                        # 禁赛判负（与淘汰赛"禁赛=不能靠票赢"同一语义）：
                        # 对手兵不血刃取胜，禁赛方计 1 负，票数忽略
                        w, l = (cid2, cid1) if b1 else (cid1, cid2)
                        result[w]['wins'] += 1
                        result[l]['losses'] += 1
                        continue

                    # 正常场次：从 vote_map 取票数，不再查数据库
                    votes1 = vote_map.get((cid1, round_num), 0)
                    votes2 = vote_map.get((cid2, round_num), 0)

                    result[cid1]['total_votes'] += votes1
                    result[cid2]['total_votes'] += votes2

                    if votes1 > votes2:
                        result[cid1]['wins'] += 1
                        result[cid2]['losses'] += 1
                    elif votes1 < votes2:
                        result[cid2]['wins'] += 1
                        result[cid1]['losses'] += 1
                    else:
                        result[cid1]['draws'] += 1
                        result[cid2]['draws'] += 1

        for cid, data in result.items():
            data['points'] = data['wins'] * 3 + data['draws'] * 1

        return result, groups

    def get_top2_from_groups(result, groups):
        top16 = []
        top16_info = []
        for gi, group in enumerate(groups):
            group_candidates = [cid for cid in group if cid in result]
            # 过滤掉被禁赛的角色
            qualified = [cid for cid in group_candidates if result[cid]['candidate'].banned == False]
            qualified.sort(key=lambda cid: (result[cid]['points'], result[cid]['total_votes']), reverse=True)
            top2 = qualified[:2] if qualified else []
            for rank, cid in enumerate(top2, start=1):
                top16.append(cid)
                top16_info.append({'id': cid, 'group_index': gi, 'rank': rank})
                if cid in result:
                    result[cid]['candidate'].stage = 'knockout'
        return top16, top16_info

    def generate_knockout_matches(candidate_inputs):
        if not candidate_inputs:
            return []

        if isinstance(candidate_inputs[0], (int, str)):
            shuffled = list(candidate_inputs)
            random.shuffle(shuffled)
            matches = []
            for i in range(0, len(shuffled), 2):
                if i + 1 < len(shuffled):
                    matches.append({
                        'round': 'round_1',
                        'round_name': '16强',
                        'candidate1': shuffled[i],
                        'candidate2': shuffled[i + 1],
                        'status': 'active',
                        'votes1': 0,
                        'votes2': 0,
                        'winner': None
                    })
                else:
                    # 奇数人兜底（小组赛禁赛连锁）：最后一人轮空，不再静默淘汰
                    matches.append({
                        'round': 'round_1',
                        'round_name': '16强',
                        'candidate1': shuffled[i],
                        'candidate2': None,
                        'status': 'active',
                        'votes1': 0,
                        'votes2': 0,
                        'winner': None,
                        'bye': True
                    })
            return matches

        infos = list(candidate_inputs)
        winners = [i for i in infos if i.get('rank') == 1]
        runners = [i for i in infos if i.get('rank') == 2]

        if len(winners) + len(runners) < 16:
            ids = [i.get('id') for i in infos]
            shuffled = ids[:]
            random.shuffle(shuffled)
            matches = []
            for i in range(0, len(shuffled), 2):
                if i + 1 < len(shuffled):
                    matches.append({
                        'round': 'round_1',
                        'round_name': '16强',
                        'candidate1': shuffled[i],
                        'candidate2': shuffled[i + 1],
                        'status': 'active',
                        'votes1': 0,
                        'votes2': 0,
                        'winner': None
                    })
                else:
                    # 奇数人兜底：最后一人轮空，不再静默淘汰
                    matches.append({
                        'round': 'round_1',
                        'round_name': '16强',
                        'candidate1': shuffled[i],
                        'candidate2': None,
                        'status': 'active',
                        'votes1': 0,
                        'votes2': 0,
                        'winner': None,
                        'bye': True
                    })
            return matches

        random.shuffle(winners)
        random.shuffle(runners)

        matches = []
        remaining_runners = runners[:]
        for w in winners:
            pick_idx = None
            for idx, r in enumerate(remaining_runners):
                if r.get('group_index') != w.get('group_index'):
                    pick_idx = idx
                    break
            if pick_idx is None:
                pick = remaining_runners.pop(0)
            else:
                pick = remaining_runners.pop(pick_idx)

            matches.append({
                'round': 'round_1',
                'round_name': '16强',
                'candidate1': w.get('id'),
                'candidate2': pick.get('id'),
                'status': 'active',
                'votes1': 0,
                'votes2': 0,
                'winner': None
            })

        return matches

    female_result, female_groups = get_group_stage_results('female')
    male_result, male_groups = get_group_stage_results('male')

    female_top16, female_top16_info = get_top2_from_groups(female_result, female_groups)
    male_top16, male_top16_info = get_top2_from_groups(male_result, male_groups)

    female_matches = generate_knockout_matches(female_top16_info)
    male_matches = generate_knockout_matches(male_top16_info)

    # 保存 16 强历史
    if female_matches:
        contest.config['knockout_matches_female_round16'] = female_matches.copy()
    if male_matches:
        contest.config['knockout_matches_male_round16'] = male_matches.copy()

    contest.config['knockout_matches_female'] = female_matches
    contest.config['knockout_matches_male'] = male_matches
    contest.config['female_top16'] = female_top16
    contest.config['male_top16'] = male_top16
    contest.config['female_top16_info'] = female_top16_info
    contest.config['male_top16_info'] = male_top16_info

    for cid in female_top16:
        candidate = session.get(Candidate, cid)
        if candidate:
            candidate.stage = 'knockout'
    for cid in male_top16:
        candidate = session.get(Candidate, cid)
        if candidate:
            candidate.stage = 'knockout'

    # 数据完整性检查：缩编赛制（入围<标准组数×4）或缺员组会合法产生非 16 人晋级，
    # 由淘汰赛轮空机制承接；此处降级为告警，不再 raise 卡死自动推进
    female_groups_cfg = contest.config.get('female_groups') or []
    if female_groups_cfg and len(female_top16) != 2 * len(female_groups_cfg):
        print(f">>> [萌战] 女子组晋级 {len(female_top16)} 人 ≠ 组数×2"
              f"（{2 * len(female_groups_cfg)}），缺额由轮空机制承接")
    male_groups_cfg = contest.config.get('male_groups') or []
    if male_groups_cfg and len(male_top16) != 2 * len(male_groups_cfg):
        print(f">>> [萌战] 男子组晋级 {len(male_top16)} 人 ≠ 组数×2"
              f"（{2 * len(male_groups_cfg)}），缺额由轮空机制承接")

    # 推进完成哨兵：两性别对阵均为空时也落真值，避免反复重排淘汰赛对阵
    contest.config['knockout_promoted'] = True

    flag_modified(contest, 'config')
    session.commit()


# ============================================================
# 淘汰赛辅助函数
# ============================================================

def is_banned(candidate_id, contest_id=None):
    """候选角色是否「不可作为胜者晋级」。

    原来各处在拿到 .banned 前都先做真值判断（如 `if c1_candidate and c1_candidate.banned`），
    候选人查不到时等于"未禁赛"，删库竞态下会放行一个不存在/已删除的角色晋级。
    这里改为 fail-closed：查不到、不属于本赛事的候选人一律视为不可晋级。
    """
    if candidate_id is None:
        return True
    candidate = db.session.get(Candidate, candidate_id)
    if candidate is None:
        return True
    if contest_id is not None and candidate.contest_id != contest_id:
        # 候选人不属于本赛事：视同不可投票/不可晋级
        return True
    return bool(candidate.banned)


def get_knockout_votes(contest, candidate_id, gender, sub_round):
    """统计单个候选人在淘汰赛本轮的总票数。

    计数必须限定在本赛事内：candidate_id 全局唯一，但缺少 contest_id 过滤时，
    任何调用方（含将来新增的）一旦传入他赛事的候选 id，就会把别的赛事的票
    算进本场对决；这里补上赛事维度，属于无副作用的收紧。
    """
    if candidate_id is None:
        return 0
    total = ContestVote.query.filter_by(
        contest_id=contest.id,
        candidate_id=candidate_id,
        round_number=4,
        sub_round=sub_round,
        gender=gender
    ).with_entities(db.func.sum(ContestVote.weight)).scalar() or 0
    return total


def get_match_winner(contest, match, gender, sub_round):
    """确定一场比赛的胜者（票数高者，平票则比较海选总票数；禁赛角色自动落败，不晋级）

    双方都被禁赛时返回 None：本场无人胜出，由 generate_next_round 用
    合法落败者补位，保证下一轮人数足够、赛事链条不断。
    """
    c1_id = match.get('candidate1')
    c2_id = match.get('candidate2')

    # 轮空/已移除槽位：只有一方存在时该方直接晋级。
    # 这里必须用 get()：淘汰赛早期场次允许只有 candidate1（bye=True，candidate2 缺失），
    # 原实现用 match['candidate2'] 取值会直接抛 KeyError，把整个自动推进打断成 500。
    if c1_id is None and c2_id is None:
        return None
    if c2_id is None:
        return None if is_banned(c1_id, contest.id) else c1_id
    if c1_id is None:
        return None if is_banned(c2_id, contest.id) else c2_id

    # 禁赛角色自动落败：有 ban 角色直接让另一方晋级（方案 A：禁赛 = 不能晋级）
    if is_banned(c1_id, contest.id) and is_banned(c2_id, contest.id):
        # 双禁赛：无人可以晋级（返回 None，交由补位机制处理）
        return None
    if is_banned(c1_id, contest.id):
        return c2_id
    if is_banned(c2_id, contest.id):
        return c1_id

    c1_votes = get_knockout_votes(contest, c1_id, gender, sub_round)
    c2_votes = get_knockout_votes(contest, c2_id, gender, sub_round)

    if c1_votes > c2_votes:
        return c1_id
    if c2_votes > c1_votes:
        return c2_id

    if gender == 'female':
        results = contest.config.get('female_result', [])
    else:
        results = contest.config.get('male_result', [])

    c1_id = match['candidate1']
    c2_id = match['candidate2']

    c1_qualifying = next((item['votes'] for item in results if item.get('id') == c1_id), 0)
    c2_qualifying = next((item['votes'] for item in results if item.get('id') == c2_id), 0)

    return c1_id if c1_qualifying >= c2_qualifying else c2_id


def generate_next_round(contest, matches, gender, sub_round, round_name):
    """根据上一轮胜者生成下一轮对阵。

    双禁赛场次无人晋级（winner=None），由该轮票数最高的合法落败者补位，
    使晋级人数恒等于场次数——16→8→4→2 每轮人数链不缺员，赛事不会走崩。
    补位池耗尽时（极端情况），奇数剩余者生成"轮空场"（candidate2=None）
    兜底，推进时对方自动晋级，不再静默淘汰。

    matches 是 JSON config 里的普通 dict 列表，轮空场可能直接缺 'candidate2' 键，
    因此全程使用 match.get()，原实现用 match['candidate2'] 会 KeyError。
    """
    winners = []
    banned_slots = 0   # 双禁赛场次数（每场需补位 1 人）
    loser_pool = []    # (本轮票数, candidate_id)：可复活的合法落败者
    for match in matches:
        # 记录本轮最终票数（供公示期展示）
        c1_id = match.get('candidate1')
        c2_id = match.get('candidate2')
        # 轮空场（另一方为 None）没有对决，票数固定为 0，不必查库
        c1_votes = get_knockout_votes(contest, c1_id, gender, sub_round) if c1_id and c2_id else 0
        c2_votes = get_knockout_votes(contest, c2_id, gender, sub_round) if c1_id and c2_id else 0
        match['votes1'] = c1_votes
        match['votes2'] = c2_votes

        winner_id = get_match_winner(contest, match, gender, sub_round)
        match['winner'] = winner_id
        match['status'] = 'finished'

        if winner_id is None:
            # 双禁赛：本场无人晋级，稍后由最佳落败者补位
            banned_slots += 1
            continue
        winners.append(winner_id)

        # 落败者进入补位池（双禁赛场双方均禁赛不进池；单禁赛场的落败者即禁赛方，也会被过滤）
        loser_id = c2_id if winner_id == c1_id else c1_id
        loser_cand = db.session.get(Candidate, loser_id) if loser_id is not None else None
        if loser_cand is not None and not loser_cand.banned:
            loser_pool.append((c2_votes if winner_id == c1_id else c1_votes, loser_id))

    if banned_slots:
        loser_pool.sort(key=lambda t: t[0], reverse=True)
        lucky = [cid for _, cid in loser_pool[:banned_slots]]
        winners.extend(lucky)
        # 在原场次上记录补位者，供展示"替补晋级"
        pending = list(lucky)
        for match in matches:
            if match.get('winner') is None and pending:
                match['repechage'] = pending.pop(0)
        if len(lucky) < banned_slots:
            print(f'>>> [萌战] {round_name}：双禁赛 {banned_slots} 场但合法落败者'
                  f'仅 {len(lucky)} 人，不足部分由轮空场兜底')

    random.shuffle(winners)
    next_matches = []
    for i in range(0, len(winners), 2):
        if i + 1 < len(winners):
            next_matches.append({
                'round': round_name,
                'round_name': round_name,
                'candidate1': winners[i],
                'candidate2': winners[i + 1],
                'status': 'active',
                'votes1': 0,
                'votes2': 0,
                'winner': None
            })
        else:
            # 奇数兜底：最后一人轮空，对方位置为 None，推进时自动晋级
            next_matches.append({
                'round': round_name,
                'round_name': round_name,
                'candidate1': winners[i],
                'candidate2': None,
                'status': 'active',
                'votes1': 0,
                'votes2': 0,
                'winner': None,
                'bye': True
            })
    return next_matches


# ============================================================
# 淘汰赛推进
# ============================================================

def run_knockout_advance(contest, now, times):
    """
    淘汰赛各轮自动推进
    返回: (是否已推进, 推进后的轮次名称或None)
    """
    if contest.config is None:
        return False, None
    if not (contest.config.get('knockout_matches_female') or contest.config.get('knockout_matches_male')):
        return False, None
    # 赛程时间缺失（contest.open_at 为空 → calc_stage_times 全返回 None）时，
    # now >= None 会抛 TypeError 直接 500；没有时间节点就不存在"到点推进"。
    if not times or not times.get('knockout_16_end'):
        return False, None

    config = contest.config

    # 当前进行中的轮次（两性别取最大推进度；空性别忽略）。
    # 幂等推进的关键：初次生成 16 强时历史键就有值，不能拿历史键当"已推进"标记
    # （原实现拿 round8 历史键守卫 16→8，而该键要到 8→4 推进时才写入，
    #  导致整个 16强公示期+8强期每个 tick 都重入，对阵链被反复砍半塌缩）。
    # 活动 match 的 round_name 是真实进度：16强(1)→8强(2)→4强(3)→决赛(4)。
    round_order = {'16强': 1, '8强': 2, '4强': 3, '决赛': 4}
    active_round = 0
    for g in ('female', 'male'):
        for m in config.get(f'knockout_matches_{g}', []) or []:
            active_round = max(active_round, round_order.get(m.get('round_name'), 0))

    # 16强 → 8强（仅当前进中的确是 16 强）
    if now >= times['knockout_16_end'] and active_round == 1:
        female_matches = config.get('knockout_matches_female', [])
        male_matches = config.get('knockout_matches_male', [])
        if not female_matches and not male_matches:
            return False, None
        if female_matches:
            config['knockout_matches_female_round16'] = female_matches.copy()
            config['knockout_matches_female'] = generate_next_round(
                contest, female_matches, 'female', 1, '8强')
        if male_matches:
            config['knockout_matches_male_round16'] = male_matches.copy()
            config['knockout_matches_male'] = generate_next_round(
                contest, male_matches, 'male', 1, '8强')
        flag_modified(contest, 'config')
        db.session.commit()
        return True, '8强'

    # 8强 → 4强（仅当前进中的确是 8 强）
    if now >= times['knockout_8_end'] and active_round == 2:
        female_matches = config.get('knockout_matches_female', [])
        male_matches = config.get('knockout_matches_male', [])
        if not female_matches and not male_matches:
            return False, None
        if female_matches:
            config['knockout_matches_female_round8'] = female_matches.copy()
            config['knockout_matches_female'] = generate_next_round(
                contest, female_matches, 'female', 2, '4强')
        if male_matches:
            config['knockout_matches_male_round8'] = male_matches.copy()
            config['knockout_matches_male'] = generate_next_round(
                contest, male_matches, 'male', 2, '4强')
        flag_modified(contest, 'config')
        db.session.commit()
        return True, '4强'

    # 4强 → 决赛（仅当前进中的确是 4 强）
    if now >= times['knockout_4_end'] and active_round == 3:
        female_matches = config.get('knockout_matches_female', [])
        male_matches = config.get('knockout_matches_male', [])
        if not female_matches and not male_matches:
            return False, None
        if female_matches:
            config['knockout_matches_female_round4'] = female_matches.copy()
            final_matches = generate_next_round(contest, female_matches, 'female', 3, '决赛')
            config['knockout_matches_female_final'] = final_matches.copy()
            config['knockout_matches_female'] = final_matches
        if male_matches:
            config['knockout_matches_male_round4'] = male_matches.copy()
            final_matches_male = generate_next_round(contest, male_matches, 'male', 3, '决赛')
            config['knockout_matches_male_final'] = final_matches_male.copy()
            config['knockout_matches_male'] = final_matches_male
        flag_modified(contest, 'config')
        db.session.commit()
        return True, '决赛'

    # 决赛投票结束 → 计票写入决赛胜者
    # （决赛没有下一轮，胜者不会经 generate_next_round 产生，必须显式计票；
    # 以 status=='active' 为幂等条件：计过票后不再重触发，双禁赛 winner=None 也不会）
    if now >= times['final_vote_end']:
        recorded = False
        for gender in ('female', 'male'):
            finals = config.get(f'knockout_matches_{gender}', []) or []
            # 决赛历史键与活动列表同序（同为 final_matches 的浅拷贝引用，
            # 但 JSON 序列化后是独立对象），必须双写，否则终榜读到 winner=None
            history = config.get(f'knockout_matches_{gender}_final', []) or []
            for idx, m in enumerate(finals):
                if m.get('status') != 'active':
                    continue
                m['votes1'] = get_knockout_votes(
                    contest, m['candidate1'], gender, 4) if m.get('candidate1') else 0
                m['votes2'] = get_knockout_votes(
                    contest, m['candidate2'], gender, 4) if m.get('candidate2') else 0
                m['winner'] = get_match_winner(contest, m, gender, 4)
                m['status'] = 'finished'
                if idx < len(history) and history[idx] is not m:
                    history[idx]['votes1'] = m['votes1']
                    history[idx]['votes2'] = m['votes2']
                    history[idx]['winner'] = m['winner']
                    history[idx]['status'] = 'finished'
                recorded = True
        if recorded:
            flag_modified(contest, 'config')
            db.session.commit()
            return True, '决赛计票'

    return False, None


# ============================================================
# 最终排名
# ============================================================

def run_final_ranking(contest):
    """决赛结束后生成最终排名并标记冠军"""
    if contest.config is None:
        contest.config = {}

    female_candidates = contest.candidates.filter_by(gender='female').filter(
        Candidate.stage.in_(['knockout', 'champion'])).all()
    male_candidates = contest.candidates.filter_by(gender='male').filter(
        Candidate.stage.in_(['knockout', 'champion'])).all()

    def get_final_ranking(_candidates, gender):
        round16_key = 'knockout_matches_female_round16' if gender == 'female' else 'knockout_matches_male_round16'
        round8_key = 'knockout_matches_female_round8' if gender == 'female' else 'knockout_matches_male_round8'
        round4_key = 'knockout_matches_female_round4' if gender == 'female' else 'knockout_matches_male_round4'
        final_key = 'knockout_matches_female_final' if gender == 'female' else 'knockout_matches_male_final'

        matches_16 = contest.config.get(round16_key, [])
        if not matches_16:
            return [], None

        matches_8 = contest.config.get(round8_key, [])
        if not matches_8:
            matches_8 = []
        matches_4 = contest.config.get(round4_key, [])
        if not matches_4:
            matches_4 = []
        matches_final = contest.config.get(final_key, [])
        if not matches_final:
            matches_final = []

        ranking = []

        def _rank_loser_id(match):
            """从已完赛场次推导负者 id。

            双禁赛（winner=None）双方均不进排名，返回 None；
            轮空场的 candidate2=None 同样返回 None，避免以 None 主键查询。
            """
            winner_id = match.get('winner')
            if winner_id is None:
                return None
            return match['candidate1'] if winner_id == match['candidate2'] else match['candidate2']

        final_match = None
        # 取最后一场"真实"决赛：轮空场垫在列表尾部（如 3 人进入决赛轮时）
        if matches_final:
            final_match = next(
                (m for m in reversed(matches_final) if not m.get('bye')), None)
        # 冠军只在决赛有合法胜者且胜者未禁赛时产生（决赛双禁赛 → 该届冠军空缺）
        champion_id = None
        if final_match and final_match.get('winner'):
            winner = db.session.get(Candidate, final_match['winner'])
            if winner and not winner.banned:
                champion_id = winner.id
                ranking.append({
                    'candidate': winner,
                    'stage': 'champion',
                    'votes': get_knockout_votes(contest, winner.id, gender, 4)
                })
            # 决赛负者进榜；方案 A：禁赛者不进最终排名
            loser_id = _rank_loser_id(final_match)
            loser = db.session.get(Candidate, loser_id) if loser_id is not None else None
            if loser and not loser.banned:
                ranking.append({
                    'candidate': loser,
                    'stage': 'finalist',
                    'votes': get_knockout_votes(contest, loser.id, gender, 4)
                })

        if matches_4 and len(matches_4) >= 2:
            for match in matches_4[:2]:
                loser_id = _rank_loser_id(match)
                loser = db.session.get(Candidate, loser_id) if loser_id is not None else None
                if loser and not loser.banned:
                    ranking.append({
                        'candidate': loser,
                        'stage': 'semifinalist',
                        'votes': get_knockout_votes(contest, loser.id, gender, 3)
                    })

        if matches_8 and len(matches_8) >= 4:
            for match in matches_8[:4]:
                loser_id = _rank_loser_id(match)
                loser = db.session.get(Candidate, loser_id) if loser_id is not None else None
                if loser and not loser.banned:
                    ranking.append({
                        'candidate': loser,
                        'stage': 'quarterfinalist',
                        'votes': get_knockout_votes(contest, loser.id, gender, 2)
                    })

        if matches_16 and len(matches_16) >= 8:
            for match in matches_16[:8]:
                loser_id = _rank_loser_id(match)
                loser = db.session.get(Candidate, loser_id) if loser_id is not None else None
                if loser and not loser.banned:
                    ranking.append({
                        'candidate': loser,
                        'stage': 'round16',
                        'votes': get_knockout_votes(contest, loser.id, gender, 1)
                    })

        stage_order = {
            'champion': 0,
            'finalist': 1,
            'semifinalist': 2,
            'quarterfinalist': 3,
            'round16': 4
        }
        ranking.sort(key=lambda x: (stage_order.get(x['stage'], 99), -x['votes']))

        result = []
        for item in ranking:
            result.append({
                'id': item['candidate'].id,
                'name': item['candidate'].name,
                'source': item['candidate'].source,
                'votes': item['votes'],
                'stage': item['stage']
            })
        return result, champion_id

    female_ranking_list, female_champion_id = get_final_ranking(female_candidates, 'female')
    male_ranking_list, male_champion_id = get_final_ranking(male_candidates, 'male')
    contest.config['female_ranking'] = female_ranking_list
    contest.config['male_ranking'] = male_ranking_list

    flag_modified(contest, 'config')

    # 加冕：直接用决赛合法胜者本人（禁赛者已被排除，不可能被加冕）
    if female_champion_id:
        champion = db.session.get(Candidate, female_champion_id)
        if champion:
            champion.stage = 'champion'
        if len(contest.config['female_ranking']) >= 2:
            runner_id = contest.config['female_ranking'][1]['id']
            runner = db.session.get(Candidate, runner_id)
            if runner:
                runner.stage = 'finalist'
    def _vacate_note(gender):
        print(f'>>> [萌战] {"女子" if gender == "female" else "男子"}组决赛未能产生合法胜者，本届冠军空缺（仅公布排名）')
    if female_ranking_list and not female_champion_id:
        _vacate_note('female')

    if male_champion_id:
        champion = db.session.get(Candidate, male_champion_id)
        if champion:
            champion.stage = 'champion'
        if len(contest.config['male_ranking']) >= 2:
            runner_id = contest.config['male_ranking'][1]['id']
            runner = db.session.get(Candidate, runner_id)
            if runner:
                runner.stage = 'finalist'
    if male_ranking_list and not male_champion_id:
        _vacate_note('male')

    db.session.commit()


# ============================================================
# 小组赛公示数据准备
# ============================================================

def prepare_group_round_data(contest, phase):
    """
    准备小组赛公示期数据
    返回: (group_round_results, overall_ranking_female, overall_ranking_male)
    """
    if phase not in ['group_round_1_result', 'group_round_2_result', 'group_round_3_result']:
        return None, None, None

    parts = phase.split('_')
    try:
        round_num = int(parts[2])
    except (IndexError, ValueError) as exc:
        raise ValueError(f"无法解析小组轮次: {phase}") from exc

    groups_female = contest.config.get('female_groups', [])
    groups_male = contest.config.get('male_groups', [])
    candidates_map = {c.id: c for c in contest.candidates.all()}

    # 批量预取票数：原先每场对决各发 2 条 SUM，8 组×多轮×2 性别可放大到上百次查询。
    _gt_rows = db.session.query(
        ContestVote.candidate_id.label('cid'),
        ContestVote.gender.label('gender'),
        sa_func.sum(ContestVote.weight).label('total')
    ).filter(
        ContestVote.contest_id == contest.id,
        ContestVote.round_number == round_num
    ).group_by(ContestVote.candidate_id, ContestVote.gender).all()
    _gt_vote_map = {(r.gender, r.cid): r.total for r in _gt_rows}

    _ov_rows = db.session.query(
        ContestVote.candidate_id.label('cid'),
        ContestVote.gender.label('gender'),
        ContestVote.round_number.label('rn'),
        sa_func.sum(ContestVote.weight).label('total')
    ).filter(
        ContestVote.contest_id == contest.id,
        ContestVote.round_number.in_([1, 2, 3])
    ).group_by(
        ContestVote.candidate_id, ContestVote.gender, ContestVote.round_number
    ).all()
    _ov_vote_map = {(r.gender, r.cid, r.rn): r.total for r in _ov_rows}

    def get_group_matches(gender, groups):
        if not groups:
            return []
        result = []
        for gi, group in enumerate(groups):
            group_name = chr(65 + gi) + '组'
            # 分组恒 4 槽（缺员以 None 占位）；防御旧数据中可能存在的短组
            slots = list(group) + [None] * (4 - len(group))
            if round_num == 1:
                pairs = [(slots[0], slots[1]), (slots[2], slots[3])]
            elif round_num == 2:
                pairs = [(slots[0], slots[2]), (slots[1], slots[3])]
            else:
                pairs = [(slots[0], slots[3]), (slots[1], slots[2])]

            matches = []
            for cid1, cid2 in pairs:
                c1 = candidates_map.get(cid1) if cid1 is not None else None
                c2 = candidates_map.get(cid2) if cid2 is not None else None
                if not c1 and not c2:
                    continue
                b1 = bool(c1.banned) if c1 else False
                b2 = bool(c2.banned) if c2 else False
                votes1 = votes2 = 0
                winner = None
                if c1 and c2 and not b1 and not b2:
                    votes1 = _gt_vote_map.get((gender, cid1), 0)
                    votes2 = _gt_vote_map.get((gender, cid2), 0)
                    winner = cid1 if votes1 > votes2 else (cid2 if votes2 > votes1 else None)
                elif c1 and c2 and b1 != b2:
                    # 单边禁赛：对手兵不血刃取胜
                    winner = cid2 if b1 else cid1
                # 双禁赛或轮空槽（任一方缺失）：winner 保持 None
                matches.append({
                    'candidate1': c1,
                    'candidate2': c2,
                    'removed1': c1 is None and cid1 is not None,
                    'removed2': c2 is None and cid2 is not None,
                    'banned1': b1,
                    'banned2': b2,
                    'bye': bool(c1 is None or c2 is None),
                    'votes1': votes1,
                    'votes2': votes2,
                    'winner': winner
                })
            result.append({'group_name': group_name, 'matches': matches})
        return result

    def get_overall_ranking(gender, groups):
        candidates = db.session.query(Candidate).filter_by(
            contest_id=contest.id,
            gender=gender,
            stage='group_stage'
        ).all()
        if not candidates:
            return []

        group_map = {}
        for gi, group in enumerate(groups):
            for cid in group:
                if cid is not None:
                    group_map[cid] = chr(65 + gi) + '组'

        stats = {}
        for c in candidates:
            stats[c.id] = {
                'name': c.name,
                'image_url': c.image_url,
                'group': group_map.get(c.id, ''),
                'banned': bool(c.banned),
                'wins': 0,
                'draws': 0,
                'losses': 0,
                'points': 0,
                'total_votes': 0,
            }

        for r in range(1, round_num + 1):
            for gi, group in enumerate(groups):
                # 4 槽不变量（缺员以 None 占位），与 config 分组槽位一致
                slots = list(group) + [None] * (4 - len(group))
                if r == 1:
                    pairs = [(slots[0], slots[1]), (slots[2], slots[3])]
                elif r == 2:
                    pairs = [(slots[0], slots[2]), (slots[1], slots[3])]
                else:
                    pairs = [(slots[0], slots[3]), (slots[1], slots[2])]

                for cid1, cid2 in pairs:
                    # 轮空槽/异常缺员：本场不存在，不计分
                    if (cid1 is None or cid2 is None
                            or cid1 not in stats or cid2 not in stats):
                        continue

                    b1 = stats[cid1]['banned']
                    b2 = stats[cid2]['banned']
                    if b1 and b2:
                        # 双禁赛：两败俱伤，本场不计分
                        continue
                    if b1 or b2:
                        # 禁赛判负，与引擎计分同语义
                        w, l = (cid2, cid1) if b1 else (cid1, cid2)
                        stats[w]['wins'] += 1
                        stats[l]['losses'] += 1
                        continue

                    votes1 = _ov_vote_map.get((gender, cid1, r), 0)

                    votes2 = _ov_vote_map.get((gender, cid2, r), 0)

                    # 累加本轮票数到 total_votes
                    stats[cid1]['total_votes'] += votes1
                    stats[cid2]['total_votes'] += votes2

                    if votes1 > votes2:
                        stats[cid1]['wins'] += 1
                        stats[cid2]['losses'] += 1
                    elif votes1 < votes2:
                        stats[cid2]['wins'] += 1
                        stats[cid1]['losses'] += 1
                    else:
                        stats[cid1]['draws'] += 1
                        stats[cid2]['draws'] += 1

        for cid, data in stats.items():
            data['points'] = data['wins'] * 3 + data['draws'] * 1

        return sorted(stats.values(), key=lambda x: (x['points'], x['total_votes']), reverse=True)

    group_round_results = {
        'female': get_group_matches('female', groups_female),
        'male': get_group_matches('male', groups_male)
    }

    overall_ranking_female = get_overall_ranking('female', groups_female)
    overall_ranking_male = get_overall_ranking('male', groups_male)

    return group_round_results, overall_ranking_female, overall_ranking_male


# ========== 当前轮次判定（投票页 / vote-stats 接口共用） ==========
def _cn_now_naive():
    from .utils import cn_now  # pylint: disable=import-outside-toplevel
    return cn_now()


def _naive_open_at(contest):
    open_at = contest.open_at
    if open_at is not None and getattr(open_at, 'tzinfo', None) is not None:
        open_at = open_at.replace(tzinfo=None)
    return open_at


def current_group_round(contest):
    """当前处于小组赛第 1/2/3 轮投票窗口则返回轮次，否则 None。"""
    if not contest or not contest.open_at:
        return None
    times = calc_stage_times(_naive_open_at(contest))
    now = _cn_now_naive()
    for n in (1, 2, 3):
        if now <= times[f'group_round_{n}_end']:
            return n
    return None


def current_knockout_sub_round(contest):
    """当前处于淘汰赛哪个子轮次（1=16强/2=8强/3=4强/4=决赛），不在窗口返回 None。"""
    if not contest or not contest.open_at:
        return None
    times = calc_stage_times(_naive_open_at(contest))
    now = _cn_now_naive()
    for n, key in ((1, 'knockout_16_end'), (2, 'knockout_8_end'),
                   (3, 'knockout_4_end'), (4, 'final_vote_end')):
        end = times.get(key)
        if end and now <= end:
            return n
    return None
