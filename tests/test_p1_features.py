"""P1：帖子/回复编辑、举报与审核队列、通知分页/清理、RSS/JSON-LD、开赛通知。"""
from app.models import AdminLog, Contest, Message, Notification, Report, Reply, User


def _thread(db_session, user, title='可编辑帖子'):
    msg = Message(title=title, content='原始正文', nickname=user.username, user_id=user.id)
    db_session.add(msg)
    db_session.commit()
    db_session.refresh(msg)
    return msg


class TestEditBoard:
    def test_author_can_edit_own_thread(self, logged_in_client, db_session, sample_user):
        msg = _thread(db_session, sample_user)
        resp = logged_in_client.post(f'/board/{msg.id}/edit', data={
            'title': '修改后的标题', 'content': '修改后的正文'}, follow_redirects=True)
        assert resp.status_code == 200
        db_session.refresh(msg)
        assert msg.title == '修改后的标题'
        assert msg.content == '修改后的正文'
        assert msg.edited_at is not None
        assert '（已编辑）' in resp.text

    def test_edit_page_loads(self, logged_in_client, db_session, sample_user):
        msg = _thread(db_session, sample_user)
        body = logged_in_client.get(f'/board/{msg.id}/edit').text
        assert '编辑帖子' in body
        assert '原始正文' in body

    def test_cannot_edit_others_thread(self, logged_in_client, db_session):
        other = User(username='别人', qq='123000111', email='other2@example.com')
        other.set_password('x')
        db_session.add(other)
        db_session.commit()
        msg = _thread(db_session, other)
        resp = logged_in_client.post(f'/board/{msg.id}/edit', data={
            'title': '篡改', 'content': '篡改'})
        assert resp.status_code == 403

    def test_edit_rejects_empty_content(self, logged_in_client, db_session, sample_user):
        msg = _thread(db_session, sample_user)
        logged_in_client.post(f'/board/{msg.id}/edit', data={'title': 'ok', 'content': '  '})
        db_session.refresh(msg)
        assert msg.content == '原始正文'

    def test_author_can_edit_own_reply(self, logged_in_client, db_session, sample_user):
        msg = _thread(db_session, sample_user)
        reply = Reply(nickname=sample_user.username, content='旧回复',
                      message_id=msg.id, user_id=sample_user.id)
        db_session.add(reply)
        db_session.commit()
        logged_in_client.post(f'/reply/{reply.id}/edit', data={'content': '新回复'})
        db_session.refresh(reply)
        assert reply.content == '新回复'
        assert reply.edited_at is not None


class TestReports:
    def test_report_page_and_create(self, logged_in_client, db_session):
        other = User(username='被举报人', qq='555000111', email='reported@example.com')
        other.set_password('x')
        db_session.add(other)
        db_session.commit()
        msg = _thread(db_session, other)

        page = logged_in_client.get(f'/report?type=thread&id={msg.id}')
        assert page.status_code == 200
        assert '举报内容' in page.text

        resp = logged_in_client.post(f'/report?type=thread&id={msg.id}',
                                     data={'reason': '广告/刷屏', 'detail': '刷广告'})
        assert resp.status_code == 302
        report = Report.query.first()
        assert report is not None
        assert report.target_type == 'thread'
        assert report.status == 'pending'

    def test_duplicate_report_ignored(self, logged_in_client, db_session):
        other = User(username='被举报2', qq='555000222', email='r2@example.com')
        other.set_password('x')
        db_session.add(other)
        db_session.commit()
        msg = _thread(db_session, other)
        for _ in range(2):
            logged_in_client.post(f'/report?type=thread&id={msg.id}',
                                  data={'reason': '广告/刷屏'})
        assert Report.query.count() == 1

    def test_cannot_report_own_content(self, logged_in_client, db_session, sample_user):
        msg = _thread(db_session, sample_user)
        logged_in_client.post(f'/report?type=thread&id={msg.id}', data={'reason': '广告/刷屏'})
        assert Report.query.count() == 0

    def test_report_requires_login(self, client, db_session, sample_user):
        msg = _thread(db_session, sample_user)
        resp = client.get(f'/report?type=thread&id={msg.id}')
        assert resp.status_code == 302


class TestAdminReports:
    def _make_report(self, db_session, sample_user, target_type='thread', target_id=1):
        report = Report(reporter_id=sample_user.id, target_type=target_type,
                        target_id=target_id, reason='广告/刷屏')
        db_session.add(report)
        db_session.commit()
        db_session.refresh(report)
        return report

    def test_admin_reports_page_loads(self, admin_client, db_session, sample_user):
        other = User(username='被举报3', qq='555000333', email='r3@example.com')
        other.set_password('x')
        db_session.add(other)
        db_session.commit()
        msg = _thread(db_session, other)
        self._make_report(db_session, sample_user, 'thread', msg.id)
        body = admin_client.get('/admin/reports').text
        assert '举报审核' in body
        assert '可编辑帖子' in body

    def test_dismiss_report(self, admin_client, db_session, sample_user):
        report = self._make_report(db_session, sample_user, 'photo', 999)
        admin_client.post(f'/admin/reports/{report.id}/handle', data={'action': 'dismiss'})
        db_session.refresh(report)
        assert report.status == 'dismissed'
        assert report.handled_by is not None

    def test_delete_target_and_resolve(self, admin_client, db_session, sample_user):
        msg = _thread(db_session, sample_user)
        report = self._make_report(db_session, sample_user, 'thread', msg.id)
        admin_client.post(f'/admin/reports/{report.id}/handle', data={'action': 'delete'})
        db_session.refresh(report)
        assert report.status == 'resolved'
        assert db_session.get(Message, msg.id) is None

    def test_member_cannot_access_reports(self, logged_in_client):
        resp = logged_in_client.get('/admin/reports')
        assert resp.status_code in (302, 403)


class TestNotificationsPagination:
    def test_notifications_paginated(self, logged_in_client, db_session, sample_user):
        for i in range(35):
            db_session.add(Notification(user_id=sample_user.id, title=f'通知{i}',
                                        content='内容', type='system'))
        db_session.commit()
        page = logged_in_client.get('/notifications').text
        # 模板里 "notif-card" 还会出现在 CSS/JS 里一次，这里按 DOM 卡片数（class="notif-card"）判断
        assert page.count('class="notif-card"') == 30
        assert '第 1 / 2 页' in page
        page2 = logged_in_client.get('/notifications?page=2').text
        assert page2.count('class="notif-card"') == 5

    def test_clear_read_notifications(self, logged_in_client, db_session, sample_user):
        read = Notification(user_id=sample_user.id, title='已读的', type='system', is_read=True)
        unread = Notification(user_id=sample_user.id, title='未读的', type='system', is_read=False)
        db_session.add_all([read, unread])
        db_session.commit()
        logged_in_client.post('/notifications/clear_read')
        remaining = Notification.query.filter_by(user_id=sample_user.id).all()
        assert len(remaining) == 1
        assert remaining[0].title == '未读的'


class TestPublicMeta:
    def test_rss_feed(self, client):
        resp = client.get('/anime_news/rss.xml')
        assert resp.status_code == 200
        assert 'rss' in resp.mimetype
        body = resp.get_data(as_text=True)
        assert body.startswith('<?xml')
        assert '<rss version="2.0">' in body

    def test_home_has_og_cover(self, logged_in_client):
        body = logged_in_client.get('/home').text
        assert 'og-cover.png' in body
        assert 'summary_large_image' in body


class TestContestNotification:
    def test_opening_contest_notifies_users(self, admin_client, db_session,
                                            sample_contest, sample_user, app):
        from app.admin import _notify_contest_open  # pylint: disable=import-outside-toplevel
        from app.models import db as _db  # pylint: disable=import-outside-toplevel

        contest_id = sample_contest.id
        with app.app_context():
            contest = _db.session.get(Contest, contest_id)
            assert contest is not None
            contest.status = 'open'
            _db.session.commit()
            _notify_contest_open(contest)
            count = Notification.query.filter_by(type='contest').count()
        assert count >= 1
