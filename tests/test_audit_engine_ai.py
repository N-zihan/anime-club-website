"""T5 独立复检 · 比赛引擎边界 + ai_predict 单飞冷却

覆盖 lead 指定的边界：
  · 轮空场（candidate2 缺失/为 None）→ candidate1 自动晋级，不得 KeyError
  · 双禁赛场 → 无人晋级（None），由补位机制处理
  · candidate 被删（DB 里不存在）→ fail-closed，不得放行晋级
  · 同票 → 确定性（多次调用结果一致）
  · open_at 为空 / times 缺键 → run_knockout_advance 返回 (False, None)，不得 TypeError
  · ai_predict：匿名首次仍能拿到结果；60s 内第二次 429 且不打桩模型
"""
import time

import pytest

from app import contest_engine as ce
from app.models import db, Candidate


# ---------------------------------------------------------------------------
# 引擎：轮空场 / 缺失键
# ---------------------------------------------------------------------------
def _match(c1, c2=..., **extra):
    m = {'candidate1': c1, 'round_name': '16强', 'gender': 'female'}
    if c2 is not ...:
        m['candidate2'] = c2
    m.update(extra)
    return m


def test_bye_match_missing_candidate2_key(app, sample_contest):
    """轮空场直接缺 'candidate2' 键：原实现 KeyError，现应正常返回 candidate1。"""
    with app.app_context():
        c1 = Candidate(contest_id=sample_contest.id, name='A', source='S',
                       gender='female', image_url='', stage='knockout')
        db.session.add(c1)
        db.session.commit()

        match = _match(c1.id)          # 完全没有 candidate2 键
        winner = ce.get_match_winner(sample_contest, match, 'female', 0)
        assert winner == c1.id, f'轮空场未让 candidate1 晋级: {winner!r}'


def test_bye_match_candidate2_none(app, sample_contest):
    """candidate2 显式为 None 的轮空场也应让 candidate1 晋级。"""
    with app.app_context():
        c1 = Candidate(contest_id=sample_contest.id, name='A', source='S',
                       gender='female', image_url='', stage='knockout')
        db.session.add(c1)
        db.session.commit()
        winner = ce.get_match_winner(sample_contest, _match(c1.id, None), 'female', 0)
        assert winner == c1.id


def test_both_candidates_absent_returns_none(app, sample_contest):
    """两边都缺 → 无人晋级（None），不得抛异常。"""
    with app.app_context():
        assert ce.get_match_winner(sample_contest, _match(None, None), 'female', 0) is None
        assert ce.get_match_winner(sample_contest, {'candidate1': None}, 'female', 0) is None


def test_deleted_candidate_is_fail_closed(app, sample_contest):
    """candidate 已被删除（DB 查不到）→ is_banned 视为禁赛，另一方晋级。"""
    with app.app_context():
        c1 = Candidate(contest_id=sample_contest.id, name='A', source='S',
                       gender='female', image_url='', stage='knockout')
        c2 = Candidate(contest_id=sample_contest.id, name='B', source='S',
                       gender='female', image_url='', stage='knockout')
        db.session.add_all([c1, c2])
        db.session.commit()

        assert ce.is_banned(c1.id, sample_contest.id) is False
        # 同票时若 c1 被删，应判 c1 禁赛 → c2 晋级
        deleted_id = c1.id
        db.session.delete(c1)
        db.session.commit()
        assert ce.is_banned(deleted_id, sample_contest.id) is True, \
            '已删除的 candidate 未被 fail-closed 判定为禁赛'

        winner = ce.get_match_winner(sample_contest, _match(deleted_id, c2.id), 'female', 0)
        assert winner == c2.id, f'已删除候选人仍被放行晋级: {winner!r}'


def test_candidate_from_other_contest_is_banned(app, sample_contest):
    """跨赛事的 candidate id 必须视同禁赛。"""
    with app.app_context():
        from app.models import Contest
        other = Contest(title='别的赛事', description='', type='saimoe',
                        gender_mode='separate', status='open', config={})
        db.session.add(other)
        db.session.commit()
        foreign = Candidate(contest_id=other.id, name='X', source='S',
                            gender='female', image_url='', stage='pending')
        db.session.add(foreign)
        db.session.commit()
        assert ce.is_banned(foreign.id, sample_contest.id) is True
        assert ce.is_banned(foreign.id, None) is False  # 不传 contest_id 时只查存在性


def test_none_candidate_id_is_banned(app, sample_contest):
    with app.app_context():
        assert ce.is_banned(None, sample_contest.id) is True


# ---------------------------------------------------------------------------
# 引擎：同票确定性
# ---------------------------------------------------------------------------
def test_tie_is_deterministic(app, sample_contest):
    """同票时应按海选票数决出，且多次调用结果完全一致（无随机性）。"""
    with app.app_context():
        c1 = Candidate(contest_id=sample_contest.id, name='A', source='S',
                       gender='female', image_url='', stage='knockout')
        c2 = Candidate(contest_id=sample_contest.id, name='B', source='S',
                       gender='female', image_url='', stage='knockout')
        db.session.add_all([c1, c2])
        db.session.commit()

        # 让 c2 的海选票更高
        sample_contest.config = {
            'female_result': [
                {'id': c1.id, 'votes': 10},
                {'id': c2.id, 'votes': 99},
            ]
        }
        db.session.commit()

        match = _match(c1.id, c2.id)
        winners = {ce.get_match_winner(sample_contest, match, 'female', 0) for _ in range(8)}
        assert len(winners) == 1, f'同票判定不确定（出现 {len(winners)} 种结果）: {winners}'
        assert winners.pop() == c2.id, '同票未按海选票数让高票者晋级'


def test_tie_with_no_qualifying_data_is_stable(app, sample_contest):
    """没有海选票数据时同票仍需稳定（不得随机）。"""
    with app.app_context():
        c1 = Candidate(contest_id=sample_contest.id, name='A', source='S',
                       gender='female', image_url='', stage='knockout')
        c2 = Candidate(contest_id=sample_contest.id, name='B', source='S',
                       gender='female', image_url='', stage='knockout')
        db.session.add_all([c1, c2])
        db.session.commit()
        sample_contest.config = {}
        db.session.commit()
        match = _match(c1.id, c2.id)
        winners = {ce.get_match_winner(sample_contest, match, 'female', 0) for _ in range(8)}
        assert len(winners) == 1, f'缺海选数据时同票不确定: {winners}'


# ---------------------------------------------------------------------------
# 引擎：open_at 为空 / times 缺键
# ---------------------------------------------------------------------------
def test_calc_stage_times_with_empty_open_at(app):
    with app.app_context():
        times = ce.calc_stage_times(None)
        assert isinstance(times, dict)
        assert times.get('knockout_16_end') is None


@pytest.mark.parametrize('times', [
    None,
    {},
    {'knockout_16_end': None},
])
def test_run_knockout_advance_guards_missing_times(app, sample_contest, times):
    """times 缺失/为空时不得抛 TypeError，应返回 (False, None)。"""
    with app.app_context():
        sample_contest.config = {'knockout_matches_female': [
            {'candidate1': 1, 'candidate2': 2, 'round_name': '16强', 'gender': 'female'}
        ]}
        db.session.commit()
        from datetime import datetime
        result = ce.run_knockout_advance(sample_contest, datetime(2026, 1, 1), times)
        assert result == (False, None), f'times={times!r} 返回 {result!r}'


def test_run_knockout_advance_guards_none_config(app, sample_contest):
    with app.app_context():
        sample_contest.config = None
        db.session.commit()
        from datetime import datetime
        assert ce.run_knockout_advance(sample_contest, datetime(2026, 1, 1), {}) == (False, None)


# ---------------------------------------------------------------------------
# ai_predict：匿名首次可用 + 单飞冷却
# ---------------------------------------------------------------------------
def test_ai_predict_anonymous_first_call_succeeds(app, sample_contest, monkeypatch):
    """★ 不能被修成「必须登录」：匿名首次必须仍能拿到预测。"""
    import app.public_contest as public_mod
    calls = []

    def fake(contest_id, *a, **k):
        calls.append(contest_id)
        return True, 'PREDICTION_OK', None

    monkeypatch.setattr(public_mod, 'generate_prediction', fake)
    public_mod._predict_cooldown.clear()

    client = app.test_client()
    resp = client.get(f'/contest/{sample_contest.id}/predict')
    assert resp.status_code == 200, f'匿名首次被拒（过度修复）: HTTP {resp.status_code}'
    assert resp.is_json and resp.get_json().get('prediction') == 'PREDICTION_OK'
    assert len(calls) == 1


def test_ai_predict_second_call_within_cooldown_is_429_without_model_call(
        app, sample_contest, monkeypatch):
    """60s 内第二次必须 429，且绝不触发真实模型调用（资损防护）。"""
    import app.public_contest as public_mod
    calls = []

    def fake(contest_id, *a, **k):
        calls.append(contest_id)
        return True, 'PREDICTION_OK', None

    monkeypatch.setattr(public_mod, 'generate_prediction', fake)
    public_mod._predict_cooldown.clear()

    client = app.test_client()
    first = client.get(f'/contest/{sample_contest.id}/predict')
    assert first.status_code == 200
    assert len(calls) == 1

    second = client.get(f'/contest/{sample_contest.id}/predict')
    assert second.status_code == 429, f'冷却未生效，HTTP {second.status_code}'
    assert len(calls) == 1, f'冷却窗口内仍触发了模型调用（共 {len(calls)} 次）'
    body = second.get_json()
    assert body.get('retry_after', 0) > 0, '429 未带 retry_after'


def test_ai_predict_cooldown_expires(app, sample_contest, monkeypatch):
    """冷却窗口过后应允许再次调用（不能永久锁死）。"""
    import app.public_contest as public_mod
    calls = []

    def fake(contest_id, *a, **k):
        calls.append(contest_id)
        return True, 'P2', None

    monkeypatch.setattr(public_mod, 'generate_prediction', fake)
    public_mod._predict_cooldown.clear()
    client = app.test_client()
    client.get(f'/contest/{sample_contest.id}/predict')

    # 人为把冷却时间戳往前挪，模拟窗口过期
    public_mod._predict_cooldown[sample_contest.id] = time.time() - (
        public_mod._PREDICT_COOLDOWN_SECONDS + 5)
    resp = client.get(f'/contest/{sample_contest.id}/predict')
    assert resp.status_code == 200, f'冷却过期后仍被拒: HTTP {resp.status_code}'
    assert len(calls) == 2


def test_ai_predict_failure_does_not_set_cooldown(app, sample_contest, monkeypatch):
    """模型失败时不应写入冷却时间戳（否则一次失败会锁死 60s）。"""
    import app.public_contest as public_mod
    monkeypatch.setattr(public_mod, 'generate_prediction',
                        lambda cid, *a, **k: (False, None, 'model error'))
    public_mod._predict_cooldown.clear()
    client = app.test_client()
    resp = client.get(f'/contest/{sample_contest.id}/predict')
    assert resp.status_code == 500
    assert sample_contest.id not in public_mod._predict_cooldown, \
        '模型失败却写入了冷却时间戳'
