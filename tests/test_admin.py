class TestAdmin:

    def test_admin_entry_redirects_if_not_admin(self, client):
        response = client.get('/admin/entry', follow_redirects=True)
        assert response.status_code == 200

    def test_admin_dashboard_requires_admin(self, logged_in_client):
        # 不 follow 重定向，直接检查状态码和 Location
        response = logged_in_client.get('/admin/dashboard')
        assert response.status_code == 302
        # admin_required 会重定向到 public.index（即 /home）
        assert response.headers['Location'].endswith('/home')

    def test_admin_dashboard_loads_for_admin(self, admin_client):
        response = admin_client.get('/admin/dashboard')
        assert response.status_code == 200
        assert '站长管理面板' in response.text

    def test_admin_users_requires_owner(self, logged_in_client):
        response = logged_in_client.get('/admin/users')
        assert response.status_code == 302
        # 普通用户没有 admin 权限，被 admin_required 拦截跳转到首页
        assert response.headers['Location'].endswith('/home')

    def test_admin_users_loads_for_owner(self, admin_client):
        response = admin_client.get('/admin/users')
        assert response.status_code == 200
        assert '用户管理' in response.text

    def test_admin_activities_loads(self, admin_client):
        response = admin_client.get('/admin/activities')
        assert response.status_code == 200
        assert '活动管理' in response.text

    def test_admin_gallery_loads(self, admin_client):
        response = admin_client.get('/admin/gallery')
        assert response.status_code == 200
        assert '照片墙管理' in response.text

    def test_admin_messages_loads(self, admin_client):
        response = admin_client.get('/admin/messages')
        assert response.status_code == 200
        assert '帖子管理' in response.text


    def test_admin_messages_renders_thread_and_replies(self, admin_client, db_session):
        """帖子管理要能同时列出主帖与回复（v4.33.2 起一次查询取齐，避免 N+1 超时）。"""
        from app.models import Message, Reply

        thread = Message(nickname='楼主', content='主帖内容测试', user_id=None)
        db_session.add(thread)
        db_session.commit()
        db_session.add(Reply(nickname='回复者', content='回复内容测试',
                             message_id=thread.id, user_id=None))
        db_session.commit()

        response = admin_client.get('/admin/messages')
        assert response.status_code == 200
        body = response.get_data(as_text=True)
        assert '主帖内容测试' in body
        assert '回复内容测试' in body
        assert '回复者' in body

    def test_admin_contests_manage_loads(self, admin_client):
        response = admin_client.get('/admin/contests/manage')
        assert response.status_code == 200
        assert '赛事管理' in response.text

    def test_admin_contest_create_loads(self, admin_client):
        response = admin_client.get('/admin/contests/create')
        assert response.status_code == 200
        assert '创建赛事' in response.text


from io import BytesIO
# ========== 活动增删改、照片上传、用户权限切换 ==========
from unittest.mock import patch


class TestAdminExtra:

    def test_admin_activity_add(self, admin_client, db_session):
        resp = admin_client.post('/admin/activities/add', data={
            'title': '新活动',
            'date': '2026-10-20',
            'content': '测试内容'
        }, follow_redirects=True)
        from app.models import Activity
        activity = Activity.query.filter_by(title='新活动').first()
        assert activity is not None
        assert activity.date == '2026-10-20'
        assert activity.content == '测试内容'
        assert resp.status_code == 200

    def test_admin_activity_edit(self, admin_client, sample_activity, db_session):
        resp = admin_client.post(f'/admin/activities/edit/{sample_activity.id}', data={
            'title': '修改标题',
            'date': '2026-10-21',
            'content': '修改内容'
        }, follow_redirects=True)
        from app.models import Activity
        activity = db_session.get(Activity, sample_activity.id)
        assert activity.title == '修改标题'
        assert activity.date == '2026-10-21'
        assert activity.content == '修改内容'
        assert resp.status_code == 200

    def test_admin_activity_delete(self, admin_client, sample_activity, db_session):
        with patch('app.admin.get_supabase') as mock_supabase:
            mock_supabase.return_value.storage.from_.return_value.remove.return_value = None
            resp = admin_client.post(f'/admin/activities/delete/{sample_activity.id}', follow_redirects=True)
            from app.models import Activity
            assert db_session.get(Activity, sample_activity.id) is None
            assert resp.status_code == 200

    def test_admin_gallery_upload(self, admin_client, sample_activity, db_session):
        from io import BytesIO
        with patch('app.admin.get_supabase') as mock_supabase, \
                patch('app.admin.compress_image', return_value=b'compressed_data'):
            mock_supabase.return_value.storage.from_.return_value.upload.return_value = None
            data = {
                'file': (BytesIO(b'fake image data'), 'test.jpg'),
                'activity_id': str(sample_activity.id)
            }
            resp = admin_client.post('/admin/gallery/upload', data=data,
                                     content_type='multipart/form-data', follow_redirects=True)
            from app.models import Photo
            photo = Photo.query.first()
            assert photo is not None
            assert photo.activity_id == sample_activity.id
            assert photo.uploader is not None
            assert 'jpg' in photo.filename
            assert resp.status_code == 200

    def test_admin_toggle_staff(self, admin_client, sample_user, db_session):
        from app.models import User
        user = db_session.get(User, sample_user.id)
        original = user.is_staff
        resp = admin_client.post(f'/admin/users/toggle_staff/{sample_user.id}', follow_redirects=True)
        db_session.refresh(user)
        assert user.is_staff == (not original)
        assert resp.status_code == 200

    def test_admin_toggle_owner(self, admin_client, sample_user, db_session):
        from app.models import User
        user = db_session.get(User, sample_user.id)
        original = user.is_owner
        resp = admin_client.post(f'/admin/users/toggle_owner/{sample_user.id}', follow_redirects=True)
        db_session.refresh(user)
        assert user.is_owner == (not original)
        assert resp.status_code == 200

    # ---------- 友好闭环：站长代绑邮箱 / 重置密码 ----------
    def test_admin_bind_email(self, admin_client, sample_user, db_session):
        from app.models import User
        sample_user.email = None
        db_session.commit()
        resp = admin_client.post(
            f'/admin/users/bind_email/{sample_user.id}',
            data={'email': 'bound@qq.com'}, follow_redirects=True)
        user = db_session.get(User, sample_user.id)
        assert user.email == 'bound@qq.com'
        assert resp.status_code == 200

    def test_admin_bind_email_invalid_format(self, admin_client, sample_user, db_session):
        from app.models import User
        sample_user.email = None
        db_session.commit()
        resp = admin_client.post(
            f'/admin/users/bind_email/{sample_user.id}',
            data={'email': 'not-an-email'}, follow_redirects=True)
        user = db_session.get(User, sample_user.id)
        assert user.email is None  # 拒绝写入
        assert resp.status_code == 200

    def test_admin_bind_email_rejects_duplicate(self, admin_client, sample_user, db_session):
        from app.models import User
        holder = User(username='holder', qq='333333333')
        holder.set_password('pass123')
        holder.email = 'dup@qq.com'
        db_session.add(holder)
        db_session.commit()
        sample_user.email = None
        db_session.commit()
        resp = admin_client.post(
            f'/admin/users/bind_email/{sample_user.id}',
            data={'email': 'dup@qq.com'}, follow_redirects=True)
        user = db_session.get(User, sample_user.id)
        assert user.email is None  # 已被占用，拒绝
        assert resp.status_code == 200

    def test_admin_reset_password_sends_link(self, admin_client, sample_user, db_session):
        from app.models import User
        sample_user.email = 'reset@qq.com'
        db_session.commit()
        with patch('app.admin.send_reset_email', return_value=True) as mocked:
            resp = admin_client.post(
                f'/admin/users/reset_password/{sample_user.id}', follow_redirects=True)
            user = db_session.get(User, sample_user.id)
            assert user.reset_token is not None
            assert mocked.call_count == 1
            assert resp.status_code == 200

    def test_admin_reset_password_requires_email(self, admin_client, sample_user, db_session):
        from app.models import User
        sample_user.email = None
        db_session.commit()
        with patch('app.admin.send_reset_email', return_value=True) as mocked:
            resp = admin_client.post(
                f'/admin/users/reset_password/{sample_user.id}', follow_redirects=True)
            user = db_session.get(User, sample_user.id)
            assert user.reset_token is None   # 未绑邮箱不发
            assert mocked.call_count == 0
            assert resp.status_code == 200

    def test_admin_bind_email_blocked_for_member(self, logged_in_client, sample_user, db_session):
        from app.models import User
        sample_user.email = None
        db_session.commit()
        resp = logged_in_client.post(
            f'/admin/users/bind_email/{sample_user.id}',
            data={'email': 'nope@qq.com'}, follow_redirects=False)
        assert resp.status_code == 302  # owner_required → 未授权重定向
        user = db_session.get(User, sample_user.id)
        assert user.email is None

    # ---------- 禁赛功能 ----------
    def test_admin_candidate_ban(self, admin_client, sample_candidate, db_session):
        from app.models import Candidate
        c = db_session.get(Candidate, sample_candidate.id)
        c.banned = False
        db_session.commit()
        resp = admin_client.post(f'/admin/candidates/ban/{sample_candidate.id}', follow_redirects=True)
        c = db_session.get(Candidate, sample_candidate.id)
        assert c.banned is True
        assert resp.status_code == 200

    def test_admin_candidate_unban(self, admin_client, sample_candidate, db_session):
        from app.models import Candidate
        c = db_session.get(Candidate, sample_candidate.id)
        c.banned = True
        db_session.commit()
        resp = admin_client.post(f'/admin/candidates/unban/{sample_candidate.id}', follow_redirects=True)
        c = db_session.get(Candidate, sample_candidate.id)
        assert c.banned is False
        assert resp.status_code == 200
