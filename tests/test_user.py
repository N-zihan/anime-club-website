from app.models import User, db
from datetime import datetime, timedelta
from io import BytesIO
from unittest.mock import patch


class TestUser:
    """用户中心测试"""

    def test_profile_requires_login(self, client):
        # 未登录应重定向
        response = client.get('/profile', follow_redirects=False)
        assert response.status_code == 302

    def test_profile_page_loads(self, logged_in_client, sample_user):
        response = logged_in_client.get('/profile')
        assert response.status_code == 200
        assert '个人设置' in response.text

    def test_user_profile_page(self, logged_in_client, sample_user):
        response = logged_in_client.get(f'/user?name={sample_user.username}')
        assert response.status_code == 200
        assert sample_user.username in response.text

    def test_user_profile_not_found(self, logged_in_client):
        response = logged_in_client.get('/user?name=nonexistent')
        assert response.status_code == 404

    def test_get_avatar(self, logged_in_client, sample_user):
        response = logged_in_client.get(f'/avatar/{sample_user.id}')
        assert response.status_code == 200

    def test_get_avatar_fallback_is_initial_svg(self, logged_in_client, sample_user):
        """没有自定义头像时返回「首字 + 渐变」SVG，而不是一块空白/1×1 透明图"""
        assert not sample_user.avatar_url
        response = logged_in_client.get(f'/avatar/{sample_user.id}')
        assert response.status_code == 200
        assert response.mimetype == 'image/svg+xml'
        body = response.get_data(as_text=True)
        assert body.startswith('<svg')
        assert 'linearGradient' in body
        assert sample_user.username[:1].upper() in body
        assert 'max-age=86400' in response.headers.get('Cache-Control', '')

    def test_get_avatar_default(self, logged_in_client):
        response = logged_in_client.get('/avatar/99999')
        assert response.status_code == 404

    def test_change_username(self, logged_in_client, sample_user):
        response = logged_in_client.post('/profile', data={
            'action': 'change_username',
            'new_username': 'newname123'
        }, follow_redirects=True)
        assert response.status_code == 200
        sample_user = db.session.get(User, sample_user.id)
        assert sample_user.username == 'newname123'

    def test_change_password(self, logged_in_client, sample_user):
        response = logged_in_client.post('/change_password', data={
            'old_password': 'password123',
            'new_password': 'newpass456',
            'confirm_password': 'newpass456'
        }, follow_redirects=True)
        assert response.status_code == 200
        sample_user = db.session.get(User, sample_user.id)
        assert sample_user.check_password('newpass456') is True


# ========== 头像上传、邮箱绑定、用户名冲突 ==========
import io
from unittest.mock import patch


class TestUserExtra:

    def test_change_avatar_success(self, logged_in_client, sample_user, db_session):
        data = {
            'action': 'change_avatar',
            'avatar': (BytesIO(b'fake image data'), 'test.jpg')
        }
        fake_url = 'https://fake.supabase.co/avatars/test.jpg'
        with patch('app.user.compress_image', return_value=b'compressed'), \
                patch('app.user.upload_avatar_to_storage', return_value=fake_url):
            resp = logged_in_client.post('/profile', data=data, content_type='multipart/form-data',
                                         follow_redirects=True)
            from app.models import User
            user = db_session.get(User, sample_user.id)
            assert user.avatar_url == fake_url
            assert resp.status_code == 200

    def test_change_avatar_invalid_file(self, logged_in_client, sample_user, db_session):
        data = {
            'action': 'change_avatar',
            'avatar': (BytesIO(b'fake'), 'test.exe')
        }
        resp = logged_in_client.post('/profile', data=data, content_type='multipart/form-data', follow_redirects=True)
        from app.models import User
        user = db_session.get(User, sample_user.id)
        # 头像未被修改（仍为 None）
        assert user.avatar_url is None
        assert resp.status_code == 200

    def test_change_username_conflict(self, logged_in_client, sample_user, db_session):
        from app.models import User
        # 创建另一个用户
        user2 = User(username='existing', qq='111111111')
        user2.set_password('pass')
        db_session.add(user2)
        db_session.commit()
        resp = logged_in_client.post('/profile', data={
            'action': 'change_username',
            'new_username': 'existing'
        }, follow_redirects=True)
        user = db_session.get(User, sample_user.id)
        # 用户名不应改变
        assert user.username == 'testuser'
        assert resp.status_code == 200

    def test_bind_email_send_code(self, logged_in_client, sample_user):
        with patch('app.user.send_verification_email', return_value=True):
            resp = logged_in_client.post('/bind_email', data={'action': 'send_email_code', 'email': 'new@qq.com'})
            assert resp.json['success'] is True

    def test_bind_email_verify_code(self, app, logged_in_client, sample_user, db_session):
        from app.utils import code_digest
        with app.app_context():
            digest = code_digest('123456')
        with logged_in_client.session_transaction() as sess:
            sess['profile_email_digest'] = digest
            sess['profile_pending_email'] = 'new@qq.com'
            sess['profile_email_expires'] = (datetime.now() + timedelta(minutes=5)).isoformat()
        resp = logged_in_client.post('/bind_email', data={'action': 'verify_email_code', 'code': '123456'})
        assert resp.json['success'] is True
        assert resp.json['email'] == 'new@qq.com'
        from app.models import User
        user = db_session.get(User, sample_user.id)
        assert user.email == 'new@qq.com'

    def test_email_code_stored_as_digest_only(self, logged_in_client):
        """回归：验证码只能以摘要形式存在于 session。

        前端可以自行 base64 解码 Flask 的 session cookie，原实现直接存明文，
        等于邮箱验证码形同虚设。
        """
        with patch('app.user.send_verification_email', return_value=True):
            logged_in_client.post('/bind_email',
                                  data={'action': 'send_email_code', 'email': 'new@qq.com'})
        with logged_in_client.session_transaction() as sess:
            assert 'profile_email_code' not in sess
            assert 'profile_email_digest' in sess


# ========== 账号级偏好：通知免打扰（阶段 B） ==========
from app.models import Notification
from app.preferences import (NOTIFY_TYPES, get_preferences, muted_types,
                             normalize_preferences, parse_preferences,
                             save_preferences, should_send_mail)


class TestNotificationPreferences:

    def test_parse_preferences_tolerates_empty_and_dirty_values(self):
        for raw in (None, '', 'not json', '"just a string"', '[]', '123'):
            prefs = parse_preferences(raw)
            assert prefs['notify']['muted_types'] == []
            assert prefs['notify']['email_enabled'] is True
            assert prefs['notify']['badge_poll_seconds'] == 60

    def test_normalize_whitelists_types_and_poll_interval(self):
        prefs = normalize_preferences({'notify': {
            'muted_types': ['audit', '不存在的类型', 'audit'],
            'badge_poll_seconds': '999',
            'email_enabled': 'false',
        }})
        assert prefs['notify']['muted_types'] == ['audit']
        assert prefs['notify']['badge_poll_seconds'] == 60   # 非法值回落默认
        assert prefs['notify']['email_enabled'] is False

        ok = normalize_preferences({'notify': {'badge_poll_seconds': 300}})
        assert ok['notify']['badge_poll_seconds'] == 300

    def test_normalize_keeps_unknown_groups(self):
        """未来新增分组不能被保存动作抹掉"""
        prefs = normalize_preferences({'news': {'translation': 'original'},
                                       'notify': {'muted_types': ['reply']}})
        assert prefs['news']['translation'] == 'original'
        assert prefs['notify']['muted_types'] == ['reply']

    def test_guest_cannot_save_notification_settings(self, client):
        resp = client.post('/settings/notifications', data={'muted_types': ['audit']})
        assert resp.status_code == 302
        assert '/login' in resp.headers['Location']

    def test_save_notification_settings_persists(self, logged_in_client, sample_user, db_session):
        resp = logged_in_client.post('/settings/notifications', data={
            'muted_types': ['audit', 'system'],
            'badge_poll_seconds': '120',
            # email_enabled / badge_enabled 未提交 == 未勾选
        })
        assert resp.status_code == 302
        user = db_session.get(User, sample_user.id)
        prefs = get_preferences(user)
        assert prefs['notify']['muted_types'] == ['audit', 'system']
        assert prefs['notify']['badge_poll_seconds'] == 120
        assert prefs['notify']['email_enabled'] is False
        assert prefs['notify']['badge_enabled'] is False

        page = logged_in_client.get('/settings')
        assert page.status_code == 200
        assert 'value="audit"' in page.text

    def test_saved_preferences_render_back_on_settings_page(self, logged_in_client, sample_user, db_session):
        logged_in_client.post('/settings/notifications', data={
            'muted_types': ['reply'], 'email_enabled': 'on',
            'badge_enabled': 'on', 'badge_poll_seconds': '300',
        })
        page = logged_in_client.get('/settings')
        assert 'checked' in page.text
        assert 'value="300"' in page.text

    def _make_notifications(self, db_session, user):
        for ntype, title in (('reply', '有人回复了你'), ('audit', '审核未通过'), ('system', '系统维护')):
            db_session.add(Notification(user_id=user.id, title=title, type=ntype))
        db_session.commit()

    def test_muted_types_hidden_from_list_and_badge_count(self, logged_in_client, sample_user, db_session):
        self._make_notifications(db_session, sample_user)
        # 未静音时：3 条未读
        assert logged_in_client.get('/api/notifications/count').json['count'] == 3
        assert '审核未通过' in logged_in_client.get('/notifications').text

        logged_in_client.post('/settings/notifications', data={'muted_types': ['audit']})
        # 静音 audit 后：列表与红点都少了 1
        assert logged_in_client.get('/api/notifications/count').json['count'] == 2
        page = logged_in_client.get('/notifications').text
        assert '审核未通过' not in page
        assert '有人回复了你' in page
        # 静音必须有明确说明，否则用户会以为通知丢了
        assert '已静音' in page and '审核结果' in page

    def test_read_all_skips_muted_types(self, logged_in_client, sample_user, db_session):
        self._make_notifications(db_session, sample_user)
        logged_in_client.post('/settings/notifications', data={'muted_types': ['audit']})
        logged_in_client.get('/notifications/read_all')
        rows = {n.type: n.is_read for n in Notification.query.filter_by(user_id=sample_user.id).all()}
        assert rows['reply'] is True
        assert rows['system'] is True
        assert rows['audit'] is False      # 静音类型不被静默置读

    def test_preferences_are_per_user(self, logged_in_client, sample_user, db_session):
        other = User(username='other', qq='222222222')
        other.set_password('pass')
        db_session.add(other)
        db_session.commit()

        logged_in_client.post('/settings/notifications', data={'muted_types': ['audit']})
        assert muted_types(db_session.get(User, sample_user.id)) == ['audit']
        assert muted_types(db_session.get(User, other.id)) == []

    def test_muted_types_do_not_block_in_site_record(self, logged_in_client, sample_user, db_session):
        """静音只影响展示与邮件，通知本身仍然入库（取消静音后还能看到）"""
        from app.notify import notify
        logged_in_client.post('/settings/notifications', data={'muted_types': ['system']})
        notify(sample_user.id, '系统公告', notify_type='system', send_mail=False)
        db_session.commit()
        assert Notification.query.filter_by(user_id=sample_user.id, type='system').count() == 1

    def test_email_gating_by_preferences(self, sample_user, db_session):
        user = db_session.get(User, sample_user.id)
        user.email = 'member@example.com'
        db_session.commit()

        assert should_send_mail(user, 'reply') is True

        save_preferences(user, {'notify': {'muted_types': ['reply']}})
        db_session.commit()
        user = db_session.get(User, sample_user.id)
        assert should_send_mail(user, 'reply') is False
        assert should_send_mail(user, 'audit') is True

        save_preferences(user, {'notify': {'email_enabled': False}})
        db_session.commit()
        user = db_session.get(User, sample_user.id)
        assert should_send_mail(user, 'audit') is False

    def test_notify_sends_no_mail_when_type_muted(self, sample_user, db_session):
        from app.notify import notify
        user = db_session.get(User, sample_user.id)
        user.email = 'member@example.com'
        db_session.commit()
        save_preferences(user, {'notify': {'muted_types': ['reply']}})
        db_session.commit()

        with patch('app.notify.send_email') as fake_send:
            notify(sample_user.id, '回复通知', notify_type='reply', send_mail=True)
            db_session.commit()
            fake_send.assert_not_called()

        with patch('app.notify.send_email') as fake_send:
            notify(sample_user.id, '审核通知', notify_type='audit', send_mail=True)
            db_session.commit()
            fake_send.assert_called_once()

    def test_notify_all_filters_muted_users_from_mail(self, sample_user, db_session):
        from app.notify import notify_all
        muted = db_session.get(User, sample_user.id)
        muted.email = 'muted@example.com'
        save_preferences(muted, {'notify': {'email_enabled': False}})
        db_session.commit()

        with patch('app.notify.send_email_batch') as fake_batch:
            notify_all('系统公告', notify_type='system', send_mail=True)
            db_session.commit()
            assert fake_batch.call_count == 0

        # 站内通知仍然发给所有人
        assert Notification.query.filter_by(user_id=sample_user.id, type='system').count() == 1

    def test_notify_types_are_the_real_ones(self):
        assert NOTIFY_TYPES == ('reply', 'audit', 'system')


class TestAbsoluteLink:
    """邮件/通知里的相对链接转绝对链接（SITE_URL 未配置时不能硬编码个人域名）"""

    def test_uses_site_url_env(self, app, monkeypatch):
        from app.notify import _absolute_link
        monkeypatch.setenv('SITE_URL', 'https://example.com/')
        assert _absolute_link('/board/1') == 'https://example.com/board/1'

    def test_falls_back_to_request_host(self, app, monkeypatch):
        from app.notify import _absolute_link
        monkeypatch.delenv('SITE_URL', raising=False)
        with app.test_request_context('/x', base_url='https://club.test'):
            assert _absolute_link('/notifications') == 'https://club.test/notifications'

    def test_keeps_absolute_link_and_none(self, app):
        from app.notify import _absolute_link
        assert _absolute_link('https://a.test/x') == 'https://a.test/x'
        assert _absolute_link('') is None
