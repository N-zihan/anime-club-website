"""P0 改进的测试：跨实例限流、论坛回复分页、后台操作日志。"""
from datetime import datetime, timedelta, timezone

from app import throttle
from app.models import AdminLog, Message, RateLimit, Reply, User, db


class TestRateLimit:
    """限流计数落库（跨进程/实例共享）"""

    def test_throttle_allows_once_per_window(self, app):
        with app.app_context():
            key = 'unit:throttle:once'
            assert throttle.throttle_ok(key, 60) is True
            assert throttle.throttle_ok(key, 60) is False
            assert throttle.throttle_ok(key, 60) is False

    def test_throttle_resets_after_window(self, app, db_session):
        with app.app_context():
            key = 'unit:throttle:window'
            assert throttle.throttle_ok(key, 60) is True
            # 手动把窗口起点改成很久以前，模拟窗口已过期
            row = db.session.get(RateLimit, key)
            assert row is not None
            row.window_start = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=2)
            db.session.commit()
            assert throttle.throttle_ok(key, 60) is True

    def test_login_fail_limit_and_reset(self, app, db_session):
        with app.app_context():
            key = 'unit:login:fail'
            for _ in range(5):
                assert throttle.login_fail_hit_limit(key, max_failures=5, window=60) is False
            assert throttle.login_fail_hit_limit(key, max_failures=5, window=60) is True
            assert throttle.login_fail_is_limited(key, max_failures=5, window=60) is True
            throttle.login_fail_reset(key)
            assert throttle.login_fail_is_limited(key, max_failures=5, window=60) is False

    def test_counter_row_persisted(self, app, db_session):
        with app.app_context():
            throttle.throttle_ok('unit:persist', 60)
            row = db.session.get(RateLimit, 'unit:persist')
            assert row is not None and row.count == 1


class TestReplyPagination:
    def _make_thread(self, db_session, user, replies=0):
        msg = Message(title='分页测试帖', content='正文', nickname=user.username, user_id=user.id)
        db_session.add(msg)
        db_session.commit()
        for i in range(replies):
            db_session.add(Reply(nickname=user.username, content=f'回复{i + 1}',
                                 message_id=msg.id, user_id=user.id))
        db_session.commit()
        return msg

    def test_first_page_has_page_size_replies(self, logged_in_client, db_session, sample_user):
        msg = self._make_thread(db_session, sample_user, replies=35)
        body = logged_in_client.get(f'/board/{msg.id}').text
        assert body.count('class="thread-post"') >= 30
        assert '共 35 条回复' in body
        assert '第 1 / 2 页' in body

    def test_second_page_has_remainder(self, logged_in_client, db_session, sample_user):
        msg = self._make_thread(db_session, sample_user, replies=35)
        body = logged_in_client.get(f'/board/{msg.id}?page=2').text
        assert '回复35' in body
        assert '回复5' not in body
        assert '第 2 / 2 页' in body

    def test_page_out_of_range_is_clamped(self, logged_in_client, db_session, sample_user):
        msg = self._make_thread(db_session, sample_user, replies=3)
        resp = logged_in_client.get(f'/board/{msg.id}?page=99')
        assert resp.status_code == 200
        assert '第 1 / 1 页' not in resp.text
        assert '还没有回复' not in resp.text


class TestAdminLogs:
    def test_admin_action_is_logged(self, admin_client, db_session, sample_user):
        msg = Message(title='待删', content='正文', nickname=sample_user.username,
                      user_id=sample_user.id)
        db_session.add(msg)
        db_session.commit()
        admin_client.post(f'/admin/messages/delete/{msg.id}')
        log = AdminLog.query.order_by(AdminLog.id.desc()).first()
        assert log is not None
        assert log.action == '删除帖子'
        assert str(msg.id) in (log.target or '')

    def test_logs_page_loads_for_admin(self, admin_client, db_session, sample_user):
        db_session.add(AdminLog(user_id=sample_user.id, username='testuser',
                                action='测试动作', target='x=1', detail='/admin/test'))
        db_session.commit()
        body = admin_client.get('/admin/logs').text
        assert '操作日志' in body
        assert '测试动作' in body

    def test_logs_page_denied_for_member(self, logged_in_client):
        resp = logged_in_client.get('/admin/logs')
        assert resp.status_code in (302, 403)

    def test_member_post_does_not_create_admin_log(self, logged_in_client, db_session, sample_user):
        msg = Message(title='社员的帖子', content='正文', nickname=sample_user.username,
                      user_id=sample_user.id)
        db_session.add(msg)
        db_session.commit()
        before = AdminLog.query.count()
        # 普通社员尝试删自己的帖子：允许，但不是后台操作，不应写后台日志
        logged_in_client.post(f'/board/{msg.id}/delete')
        assert AdminLog.query.count() == before
