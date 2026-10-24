"""邮件周报：内容构建、收件人筛选、cron 接口鉴权与发送。"""
import json
from datetime import datetime
from unittest.mock import patch

from app import digest
from app.models import Contest, User


def _contest(db_session):
    now = datetime.now()
    contest = Contest(title='周报赛事', description='x', type='saimoe',
                      gender_mode='separate', status='open',
                      open_at=now, close_at=now, config={})
    db_session.add(contest)
    db_session.commit()
    return contest


class TestBuildDigest:
    def test_contains_news_and_contest(self, db_session):
        _contest(db_session)
        articles = [{'title_zh': '本周新闻一', 'link': 'https://news/1'},
                    {'title': 'News Two', 'url': 'https://news/2'}]
        with patch('app.digest.get_anime_news', return_value=(articles, {})):
            subject, html, text = digest.build_digest()
        assert '本周' in subject
        assert '本周新闻一' in text and 'https://news/1' in text
        assert '周报赛事' in text
        assert '本周番剧新闻' in html

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

        emails = digest.digest_recipients()
        assert 'ok@example.com' in emails
        assert 'off@example.com' not in emails
        assert all(e for e in emails)

    def test_send_counts(self, db_session):
        with patch('app.digest.build_digest', return_value=('s', '<p>x</p>', 'x')), \
                patch('app.digest.digest_recipients', return_value=['a@b.com', 'c@d.com']), \
                patch('app.auth.send_email_batch', return_value=2) as fake:
            result = digest.send_weekly_digest()
        assert result == {'recipients': 2, 'sent': 2}
        assert fake.called

    def test_no_recipients(self):
        with patch('app.digest.build_digest', return_value=('s', '<p>x</p>', 'x')), \
                patch('app.digest.digest_recipients', return_value=[]):
            assert digest.send_weekly_digest() == {'recipients': 0, 'sent': 0}


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
        friday = datetime(2026, 10, 23, 9, 0)  # 周五
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
        data = resp.get_json()
        assert resp.status_code == 200 and data['sent'] == 2

    def test_sends_on_monday(self, client, monkeypatch):
        monkeypatch.setenv('CRON_SECRET', 'top-secret')
        monday = datetime(2026, 10, 19, 9, 0)
        with patch('app.public_misc.cn_now', return_value=monday), \
                patch('app.digest.send_weekly_digest',
                      return_value={'recipients': 1, 'sent': 1}):
            resp = client.get('/api/cron/weekly-digest',
                              headers={'Authorization': 'Bearer top-secret'})
        assert resp.get_json()['sent'] == 1
