"""P2：PWA 离线页、无障碍、分享海报、追番清单。"""
from datetime import datetime, timedelta

from app.models import AnimeListEntry, Candidate, Contest, ContestVote, User


class TestPwaOffline:
    def test_offline_page_public(self, client):
        resp = client.get('/offline')
        assert resp.status_code == 200
        assert '网络开小差' in resp.get_data(as_text=True)

    def test_service_worker_has_offline_fallback(self, client):
        resp = client.get('/sw.js')
        assert resp.status_code == 200
        js = resp.get_data(as_text=True)
        assert OFFLINE_MARK in js
        assert "request.mode === 'navigate'" in js

    def test_manifest_ok(self, client):
        resp = client.get('/manifest.json')
        assert resp.status_code == 200
        assert resp.is_json


OFFLINE_MARK = "'/offline'"


class TestAccessibility:
    def test_skip_link_and_main_target(self, logged_in_client):
        body = logged_in_client.get('/home').text
        assert 'class="skip-link"' in body
        assert 'href="#main-content"' in body
        assert 'id="main-content"' in body
        assert 'tabindex="-1"' in body

    def test_flash_is_announced(self, logged_in_client, db_session, sample_user):
        from app.models import Message  # pylint: disable=import-outside-toplevel
        # 发一个无效操作产生 flash
        msg = Message(title='无障碍', content='x', nickname=sample_user.username,
                      user_id=sample_user.id)
        db_session.add(msg)
        db_session.commit()
        resp = logged_in_client.post(f'/board/{msg.id}/delete', follow_redirects=True)
        assert 'role="status"' in resp.get_data(as_text=True)


def _contest_with_votes(db_session, user):
    now = datetime.now()
    contest = Contest(title='海报测试赛事', description='测试', type='saimoe',
                      gender_mode='separate', status='open',
                      open_at=now - timedelta(days=10), close_at=now + timedelta(days=20),
                      config={})
    db_session.add(contest)
    db_session.commit()
    cands = [Candidate(contest_id=contest.id, name=f'海报角色{i}', source='示例',
                       gender='female', stage='pending') for i in range(3)]
    db_session.add_all(cands)
    db_session.commit()
    for cand, weight in zip(cands, [9, 5, 2]):
        db_session.add(ContestVote(contest_id=contest.id, candidate_id=cand.id,
                                   user_id=user.id, weight=weight,
                                   round_number=0, gender='female'))
    db_session.commit()
    return contest, cands


class TestSharePoster:
    def test_poster_button_and_data(self, logged_in_client, db_session, sample_user):
        contest, cands = _contest_with_votes(db_session, sample_user)
        body = logged_in_client.get(f'/contest/{contest.id}').text
        assert 'posterBtn' in body
        assert 'share-poster.js' in body
        assert 'id="posterData"' in body
        assert '海报测试赛事' in body
        # 数据里带着票数排行
        assert '"votes": 9' in body
        assert 'poster-btn' in body


class TestAnimeList:
    def test_add_and_update(self, logged_in_client, db_session, sample_user):
        payload = {'bangumi_id': 1234, 'title': '测试番剧', 'image': 'https://x/cover.jpg',
                   'status': 'want'}
        data = logged_in_client.post('/api/anime_list', json=payload).get_json()
        assert data['ok'] is True
        entry = AnimeListEntry.query.filter_by(user_id=sample_user.id, bangumi_id=1234).first()
        assert entry is not None and entry.status == 'want'

        # 重复加入只更新状态，不新增
        data2 = logged_in_client.post('/api/anime_list', json={
            'bangumi_id': 1234, 'title': '测试番剧', 'status': 'watching'}).get_json()
        assert data2['ok'] is True
        assert AnimeListEntry.query.filter_by(user_id=sample_user.id, bangumi_id=1234).count() == 1
        assert data2['status'] == 'watching'

    def test_rating_and_delete(self, logged_in_client, db_session, sample_user):
        logged_in_client.post('/api/anime_list', json={
            'bangumi_id': 777, 'title': '评分番剧'})
        entry = AnimeListEntry.query.filter_by(user_id=sample_user.id, bangumi_id=777).first()
        logged_in_client.post(f'/api/anime_list/{entry.id}', json={'rating': 8})
        db_session.refresh(entry)
        assert entry.rating == 8

        logged_in_client.post(f'/api/anime_list/{entry.id}', json={'action': 'delete'})
        assert db_session.get(AnimeListEntry, entry.id) is None

    def test_requires_login(self, client):
        # 全局登录拦截器会先把访客重定向到登录页（302），不会进入接口本身
        assert client.post('/api/anime_list', json={'bangumi_id': 1, 'title': 'x'}).status_code == 302

    def test_cannot_touch_others_entry(self, logged_in_client, db_session):
        other = User(username='追番别人', qq='321000111', email='otherlist@example.com')
        other.set_password('x')
        db_session.add(other)
        db_session.commit()
        entry = AnimeListEntry(user_id=other.id, bangumi_id=1, title='别人的')
        db_session.add(entry)
        db_session.commit()
        resp = logged_in_client.post(f'/api/anime_list/{entry.id}', json={'action': 'delete'})
        assert resp.status_code == 404

    def test_page_and_guide_buttons(self, logged_in_client):
        page = logged_in_client.get('/my/anime_list')
        assert page.status_code == 200
        assert '我的追番' in page.text
        guide = logged_in_client.get('/anime_resources')
        assert 'anime-status-btn' in guide.text
        assert 'addAnimeToList' in guide.text
