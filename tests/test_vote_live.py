"""投票页实时票数 + 对抗进度条：接口与页面标记测试。"""
import re
from datetime import datetime, timedelta
from pathlib import Path

from app.models import Candidate, Contest, ContestVote
from app.utils import cn_now


def _make_contest(db_session, days_ago, status='open'):
    now = datetime.now()
    contest = Contest(
        title=f'实时票数测试-{days_ago}',
        description='测试',
        type='saimoe',
        gender_mode='separate',
        status=status,
        open_at=now - timedelta(days=days_ago),
        close_at=now + timedelta(days=30),
        config={},
    )
    db_session.add(contest)
    db_session.commit()
    candidates = [
        Candidate(contest_id=contest.id, name=f'角色{i}', source='作品',
                  gender='female', stage='group')
        for i in range(8)
    ]
    db_session.add_all(candidates)
    db_session.commit()
    return contest, candidates


def _vote(db_session, contest, user, candidate, weight, **kwargs):
    db_session.add(ContestVote(contest_id=contest.id, candidate_id=candidate.id,
                               user_id=user.id, weight=weight, gender='female', **kwargs))
    db_session.commit()


class TestVoteStatsApi:
    def test_requires_login(self, client, db_session, sample_user):
        contest, _ = _make_contest(db_session, 15)
        resp = client.get(f'/api/contest/{contest.id}/vote-stats?mode=group&gender=female')
        assert resp.status_code == 302

    def test_invalid_params(self, logged_in_client, db_session, sample_user):
        contest, _ = _make_contest(db_session, 15)
        resp = logged_in_client.get(f'/api/contest/{contest.id}/vote-stats?mode=nope')
        assert resp.status_code == 400
        resp2 = logged_in_client.get(
            f'/api/contest/{contest.id}/vote-stats?mode=group&gender=other')
        assert resp2.status_code == 400

    def test_group_round_totals(self, logged_in_client, db_session, sample_user):
        contest, cands = _make_contest(db_session, 15)
        _vote(db_session, contest, sample_user, cands[0], 12, round_number=1)
        _vote(db_session, contest, sample_user, cands[1], 5, round_number=1)
        # 第 2 轮的票不应计入当前（第 1 轮）
        _vote(db_session, contest, sample_user, cands[2], 99, round_number=2)

        data = logged_in_client.get(
            f'/api/contest/{contest.id}/vote-stats?mode=group&gender=female').get_json()
        assert data['ok'] is True
        assert data['round'] == 1
        assert data['totals'][str(cands[0].id)] == 12
        assert data['totals'][str(cands[1].id)] == 5
        assert str(cands[2].id) not in data['totals']
        assert data['total'] == 17
        assert data['updated_at']

    def test_knockout_sub_round_totals(self, logged_in_client, db_session, sample_user):
        contest, cands = _make_contest(db_session, 30)
        _vote(db_session, contest, sample_user, cands[0], 7,
              round_number=4, sub_round=1, match_index=1)
        data = logged_in_client.get(
            f'/api/contest/{contest.id}/vote-stats?mode=knockout&gender=female').get_json()
        assert data['round'] == 4
        assert data['sub_round'] == 1
        assert data['totals'][str(cands[0].id)] == 7

    def test_qualifying_totals(self, logged_in_client, db_session, sample_user):
        contest, cands = _make_contest(db_session, 10)
        _vote(db_session, contest, sample_user, cands[0], 4, round_number=0)
        _vote(db_session, contest, sample_user, cands[1], 2, round_number=0)
        data = logged_in_client.get(
            f'/api/contest/{contest.id}/vote-stats?mode=qualifying&gender=female').get_json()
        assert data['total'] == 6
        assert data['totals'][str(cands[0].id)] == 4


class TestVotePages:
    def test_group_page_has_vs_bars(self, logged_in_client, db_session, sample_user):
        contest, cands = _make_contest(db_session, 15, status='group_stage')
        contest.config = {'female_groups': [[c.id for c in cands[0:4]],
                                            [c.id for c in cands[4:8]]]}
        db_session.commit()
        _vote(db_session, contest, sample_user, cands[0], 9, round_number=1,
              group_index=0, match_index=1)
        _vote(db_session, contest, sample_user, cands[1], 3, round_number=1,
              group_index=0, match_index=1)

        body = logged_in_client.get(f'/contest/{contest.id}/group/female').text
        assert 'vote-vs-bar' in body
        assert 'vote-vs-left' in body and 'vote-vs-right' in body
        assert 'vote-live.js' in body
        assert 'data-mode="group"' in body
        assert 'data-poll-ms=' in body
        assert '75.0%' in body and '25.0%' in body

    def test_knockout_page_has_vs_bars(self, logged_in_client, db_session, sample_user):
        contest, cands = _make_contest(db_session, 30)
        contest.config = {'knockout_matches_female': [{
            'round': 16, 'round_name': '16强', 'status': 'active',
            'candidate1': cands[0].id, 'candidate2': cands[1].id,
        }]}
        db_session.commit()
        _vote(db_session, contest, sample_user, cands[0], 6,
              round_number=4, sub_round=1, match_index=1)
        _vote(db_session, contest, sample_user, cands[1], 2,
              round_number=4, sub_round=1, match_index=1)

        body = logged_in_client.get(f'/contest/{contest.id}/knockout/female').text
        assert 'vote-vs-bar' in body
        assert 'data-mode="knockout"' in body
        assert '75.0%' in body and '25.0%' in body

    def test_qualifying_page_has_share_bars(self, logged_in_client, db_session, sample_user):
        contest, cands = _make_contest(db_session, 10)
        _vote(db_session, contest, sample_user, cands[0], 3, round_number=0)
        _vote(db_session, contest, sample_user, cands[1], 1, round_number=0)

        body = logged_in_client.get(f'/contest/{contest.id}/qualifying/female').text
        assert 'vote-share-fill' in body
        assert 'data-mode="qualifying"' in body
        assert 'vote-live.js' in body


class TestVoteLiveClock:
    """「实时 · HH:MM:SS」必须是北京时间且每秒在走（曾经显示 UTC、且不刷新）。"""

    def test_updated_at_is_beijing_time(self, logged_in_client, db_session, sample_user):
        contest, _ = _make_contest(db_session, 15)
        data = logged_in_client.get(
            f'/api/contest/{contest.id}/vote-stats?mode=group&gender=female').get_json()
        assert re.fullmatch(r'\d{2}:\d{2}:\d{2}', data['updated_at'])

        got = datetime.strptime(data['updated_at'], '%H:%M:%S').time()
        expected = cn_now().time()
        delta = abs((datetime.combine(datetime.today(), got)
                     - datetime.combine(datetime.today(), expected)).total_seconds())
        delta = min(delta, 86400 - delta)   # 跨零点时取环形距离
        assert delta < 120, f'updated_at 不是北京时间: {data["updated_at"]} vs 北京 {expected}'

    def test_vote_live_js_has_ticking_beijing_clock(self):
        js = (Path(__file__).resolve().parent.parent
              / 'static' / 'js' / 'vote-live.js').read_text(encoding='utf-8')
        assert 'Asia/Shanghai' in js
        assert 'toLocaleTimeString' in js
        assert 'setInterval(paintState, 1000)' in js
        # 旧实现把服务端快照时间当钟表显示，且只在轮询时刷新
        assert 'setState(' not in js
