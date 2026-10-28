"""比赛详情页的候选人状态徽章：小组赛公示期不再出现「小组赛进行中」。"""
from datetime import datetime
from unittest.mock import patch

from app.models import Candidate, Contest

OPEN_AT = datetime(2026, 10, 1, 18, 0)
NOW = datetime(2026, 10, 28, 20, 0)   # 小组赛第 3 轮公示期


def _make_contest(db_session):
    contest = Contest(title='徽章测试赛', description='用于测试阶段徽章',
                      status='group_stage', open_at=OPEN_AT,
                      close_at=datetime(2026, 11, 20, 18, 0), config={})
    db_session.add(contest)
    db_session.commit()
    db_session.add_all([
        Candidate(contest_id=contest.id, name='晋级者', source='测试作品',
                  gender='female', stage='knockout'),
        Candidate(contest_id=contest.id, name='淘汰者', source='测试作品',
                  gender='female', stage='group_stage'),
    ])
    db_session.commit()
    return contest


class TestContestCandidateBadges:
    def test_group_result_phase_shows_consistent_badges(self, logged_in_client, db_session):
        """第 3 轮公示期：晋级者显示「晋级淘汰赛」，其余显示「小组赛淘汰」，不再混用「小组赛进行中」。"""
        contest = _make_contest(db_session)
        with patch('app.public_contest._cn_now', return_value=NOW):
            resp = logged_in_client.get(f'/contest/{contest.id}')
        body = resp.get_data(as_text=True)
        assert resp.status_code == 200
        assert '晋级淘汰赛' in body
        assert '小组赛淘汰' in body
        assert '小组赛进行中' not in body

    def test_voting_phase_still_says_in_progress(self, logged_in_client, db_session):
        """第 3 轮投票期（还没结束）仍应显示「小组赛进行中」。"""
        contest = _make_contest(db_session)
        with patch('app.public_contest._cn_now', return_value=datetime(2026, 10, 26, 20, 0)):
            resp = logged_in_client.get(f'/contest/{contest.id}')
        body = resp.get_data(as_text=True)
        assert resp.status_code == 200
        assert '小组赛进行中' in body
