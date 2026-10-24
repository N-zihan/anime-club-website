"""邮件周报：内容构建、收件人筛选、退订、cron 接口鉴权与发送。"""
import json
from datetime import datetime
from unittest.mock import MagicMock, patch

from app import digest
from app.models import Contest, User
from app.preferences import get_preferences


def _contest(db_session):
    now = datetime.now()
    contest = Contest(title='周报赛事', description='x', type='saimoe',
                      gender_mode='separate', status='open',
                      open_at=now, close_at=now, config={})
    db_session.add(contest)
    db_session.commit()
    return contest


class TestBuildDigest:
    def test_contains_news_excerpt_and_contest(self, db_session):
        _contest(db_session)
        articles = [{'title_zh': '本周新闻一', 'link': 'https://news/1',
                     'excerpt_zh': '这是一句摘要', 'image': 'https://img/1.jpg'},
                    {'title': 'News Two', 'url': 'https://news/2'}]
        with patch('app.digest.get_anime_news', return_value=(articles, {})):
            subject, html, text = digest.build_digest()
        assert '本周' in subject
        assert '本周新闻一' in text and '这是一句摘要' in text
        assert 'https://news/1' in text
        assert '周报赛事' in text
        assert '当前：' in text or '截止' in text
        assert '这是一句摘要' in html
        assert 'https://img/1.jpg' in html

    def test_unsubscribe_link_included(self, db_session):
        with patch('app.digest.get_anime_news', return_value=([], {})):
            _subject, html, text = digest.build_digest(
                unsubscribe_url='https://site/digest/unsubscribe/abc')
        assert 'https://site/digest/unsubscribe/abc' in html
        assert '退订' in text

    def test_fallback_when_empty(self, db_session):
        with patch('app.digest.get_anime_news', return_value=([], {})):
            subject, html, text = digest.build_digest()
        assert '没有新内容' in text
        assert '本周' in subject

    def test_news_exception_degrades(self, db_session):
        with patch('app.digest.get_anime_news', side_effect=RuntimeError('down')):
            subject, _html, text = digest.build_digest()
        assert subject and text


class TestRecipients:
    def test_only_users_with_email_and_enabled(self, db_session):
        ok = User(username='u_ok', qq='100001', email='ok@example.com')
        ok.set_password('x')
        no_mail = User(username='u_no', qq='100002', email=None)
        no_mail.set_password('x')
        disabled = User(username='u_off', qq='100003', email='off@example.com')
        disabled.set_password('x')
        disabled.preferences = json.dumps({'notify': {'email_enabled': False}})
        db_session.add_all([ok, no_mail, disabled])
        db_session.commit()

        users = digest.digest_recipients()
        emails = [u.email for u in users]
        assert 'ok@example.com' in emails
        assert 'off@example.com' not in emails


class TestSend:
    def test_send_counts_and_personal_unsubscribe(self):
        user = MagicMock()
        user.email = 'a@b.com'
        user.id = 7
        with patch('app.digest.digest_recipients', return_value=[user]), \
                patch('app.digest.build_digest', return_value=('s', '<p>x</p>', 'x')) as fake_build, \
                patch('app.auth.send_email', return_value=True) as fake_send, \
                patch('app.digest.make_unsubscribe_token', return_value='tok'):
            result = digest.send_weekly_digest(base_url='https://site')
        assert result == {'recipients': 1, 'sent': 1}
        assert fake_send.called
        assert fake_build.call_args.kwargs.get('unsubscribe_url', '').endswith('tok')

    def test_no_recipients(self):
        with patch('app.digest.digest_recipients', return_value=[]):
            assert digest.send_weekly_digest() == {'recipients': 0, 'sent': 0}


class TestUnsubscribe:
    def test_token_roundtrip(self, app):
        with app.test_request_context():
            token = digest.make_unsubscribe_token(42)
            assert digest.parse_unsubscribe_token(token) == 42
            assert digest.parse_unsubscribe_token('bad-token') is None

    def test_unsubscribe_disables_email(self, client, db_session, app):
        user = User(username='u_unsub', qq='100004', email='unsub@example.com')
        user.set_password('x')
        db_session.add(user)
        db_session.commit()
        with app.test_request_context():
            token = digest.make_unsubscribe_token(user.id)
        resp = client.get(f'/digest/unsubscribe/{token}')
        assert resp.status_code == 200
        assert '已退订' in resp.get_data(as_text=True)
        db_session.refresh(user)
        assert get_preferences(user)['notify']['email_enabled'] is False

    def test_bad_token_400(self, client):
        resp = client.get('/digest/unsubscribe/not-a-real-token')
        assert resp.status_code == 400
        assert '无效' in resp.get_data(as_text=True)


class TestCronEndpoint:
    def test_missing_secret_503(self, client, monkeypatch):
        monkeypatch.delenv('CRON_SECRET', raising=False)
        assert client.get('/api/cron/weekly-digest').status_code == 503

    def test_wrong_token_403(self, client, monkeypatch):
        monkeypatch.setenv('CRON_SECRET', 'top-secret')
        assert client.get('/api/cron/weekly-digest').status_code == 403
        assert client.get('/api/cron/weekly-digest',
                          headers={'X-Cron-Secret': 'nope'}).status_code == 403

    def test_skips_when_not_monday(self, client, monkeypatch):
        monkeypatch.setenv('CRON_SECRET', 'top-secret')
        friday = datetime(2026, 10, 23, 9, 0)
        with patch('app.public_misc.cn_now', return_value=friday):
            resp = client.get('/api/cron/weekly-digest',
                              headers={'Authorization': 'Bearer top-secret'})
        data = resp.get_json()
        assert resp.status_code == 200
        assert data['skipped'] == 'not-monday'

    def test_sends_with_force(self, client, monkeypatch):
        monkeypatch.setenv('CRON_SECRET', 'top-secret')
        friday = datetime(2026, 10, 23, 9, 0)
        with patch('app.public_misc.cn_now', return_value=friday), \
                patch('app.digest.send_weekly_digest',
                      return_value={'recipients': 2, 'sent': 2}):
            resp = client.get('/api/cron/weekly-digest?force=1',
                              headers={'X-Cron-Secret': 'top-secret'})
        assert resp.status_code == 200 and resp.get_json()['sent'] == 2

    def test_sends_on_monday(self, client, monkeypatch):
        monkeypatch.setenv('CRON_SECRET', 'top-secret')
        monday = datetime(2026, 10, 19, 9, 0)
        with patch('app.public_misc.cn_now', return_value=monday), \
                patch('app.digest.send_weekly_digest',
                      return_value={'recipients': 1, 'sent': 1}):
            resp = client.get('/api/cron/weekly-digest',
                              headers={'Authorization': 'Bearer top-secret'})
        assert resp.get_json()['sent'] == 1
