"""对阵图页面测试。"""
from datetime import datetime, timedelta

from app.models import Candidate, Contest


def _contest_with_bracket(db_session):
    now = datetime.now()
    contest = Contest(title='对阵图测试', description='测试', type='saimoe',
                      gender_mode='separate', status='knockout',
                      open_at=now - timedelta(days=30), close_at=now + timedelta(days=10),
                      config={})
    db_session.add(contest)
    db_session.commit()
    cands = [Candidate(contest_id=contest.id, name=f'选手{i}', source='作品',
                       gender='female', stage='knockout',
                       image_url=f'https://img/{i}.jpg') for i in range(4)]
    db_session.add_all(cands)
    db_session.commit()
    contest.config = {
        'knockout_matches_female_round16': [{
            'round_name': '16强', 'status': 'finished',
            'candidate1': cands[0].id, 'candidate2': cands[1].id,
            'votes1': 12, 'votes2': 5, 'winner': cands[0].id,
        }],
        'knockout_matches_female': [{
            'round_name': '8强', 'status': 'active',
            'candidate1': cands[0].id, 'candidate2': cands[2].id,
            'votes1': 3, 'votes2': 1,
        }],
    }
    db_session.commit()
    return contest, cands


class TestContestBracket:
    def test_page_renders_rounds_and_winner(self, logged_in_client, db_session):
        contest, cands = _contest_with_bracket(db_session)
        resp = logged_in_client.get(f'/contest/{contest.id}/bracket')
        assert resp.status_code == 200
        body = resp.text
        assert '对阵图' in body
        assert '16强' in body and '8强' in body
        assert '选手0' in body and '选手1' in body
        assert 'is-winner' in body          # 晋级方金色描边
        assert '12' in body and '5' in body  # 票数

    def test_gender_switch_link(self, logged_in_client, db_session):
        contest, _ = _contest_with_bracket(db_session)
        body = logged_in_client.get(f'/contest/{contest.id}/bracket?gender=female').text
        assert '男组' in body

    def test_no_matches_shows_hint(self, logged_in_client, db_session):
        now = datetime.now()
        contest = Contest(title='空对阵', description='x', type='saimoe',
                          gender_mode='separate', status='open',
                          open_at=now - timedelta(days=3), close_at=now + timedelta(days=30),
                          config={})
        db_session.add(contest)
        db_session.commit()
        body = logged_in_client.get(f'/contest/{contest.id}/bracket').text
        assert '对阵图还没生成' in body

    def test_requires_login(self, client, db_session):
        contest, _ = _contest_with_bracket(db_session)
        assert client.get(f'/contest/{contest.id}/bracket').status_code == 302

    def test_detail_has_bracket_entry(self, logged_in_client, db_session):
        contest, _ = _contest_with_bracket(db_session)
        body = logged_in_client.get(f'/contest/{contest.id}').text
        assert '/bracket' in body

def test_duplicate_round_matches_are_deduped(logged_in_client, db_session):
    """同一轮对阵同时出现在 round16 历史 key 和当前轮时，不能列两遍（否则 16 强变 32 人）。"""
    now = datetime.now()
    contest = Contest(title='重复对阵测试', description='x', type='saimoe',
                      gender_mode='separate', status='knockout',
                      open_at=now - timedelta(days=30), close_at=now + timedelta(days=10),
                      config={})
    db_session.add(contest)
    db_session.commit()
    cands = [Candidate(contest_id=contest.id, name=f'选手{i}', source='作品',
                       gender='female', stage='knockout') for i in range(4)]
    db_session.add_all(cands)
    db_session.commit()
    contest.config = {
        'knockout_matches_female_round16': [{
            'round_name': '16强', 'status': 'finished',
            'candidate1': cands[0].id, 'candidate2': cands[1].id,
            'votes1': 9, 'votes2': 3, 'winner': cands[0].id,
        }],
        'knockout_matches_female': [{
            'round': 'round_1', 'status': 'finished',
            'candidate1': cands[0].id, 'candidate2': cands[1].id,
            'votes1': 9, 'votes2': 3, 'winner': cands[0].id,
        }],
    }
    db_session.commit()

    body = logged_in_client.get(f'/contest/{contest.id}/bracket?gender=female').text
    assert body.count('class="bracket-match') == 1
