"""论坛权限：编辑只给自己的内容；运营/站长可以「管理删除」他人的内容但不显示编辑。"""
from app.models import Message, Reply


def _make_thread(db_session, author):
    thread = Message(title='权限测试主题',
                     nickname=author.username if author else '匿名',
                     content='权限测试正文',
                     user_id=author.id if author else None)
    db_session.add(thread)
    db_session.commit()
    return thread


def _make_reply(db_session, thread, author):
    reply = Reply(nickname=author.username if author else '匿名',
                  content='权限测试回复',
                  message_id=thread.id,
                  user_id=author.id if author else None)
    db_session.add(reply)
    db_session.commit()
    return reply


class TestBoardPermissions:
    def test_author_sees_edit_and_delete(self, logged_in_client, db_session, sample_user):
        thread = _make_thread(db_session, sample_user)
        body = logged_in_client.get(f'/board/{thread.id}').get_data(as_text=True)
        assert '编辑主题' in body
        assert '删除主题' in body

    def test_member_cannot_edit_or_delete_others_thread(self, logged_in_client, db_session,
                                                       sample_admin):
        thread = _make_thread(db_session, sample_admin)
        body = logged_in_client.get(f'/board/{thread.id}').get_data(as_text=True)
        assert '编辑主题' not in body
        assert '删除主题' not in body

    def test_moderator_gets_management_delete_without_edit(self, admin_client, db_session,
                                                          sample_user):
        thread = _make_thread(db_session, sample_user)
        body = admin_client.get(f'/board/{thread.id}').get_data(as_text=True)
        assert '编辑主题' not in body
        assert '删除主题（管理）' in body

    def test_moderator_cannot_edit_others_reply(self, admin_client, db_session, sample_user):
        thread = _make_thread(db_session, sample_user)
        _make_reply(db_session, thread, sample_user)
        body = admin_client.get(f'/board/{thread.id}').get_data(as_text=True)
        assert '删除（管理）' in body
        assert '保存修改' not in body

    def test_author_can_edit_own_reply(self, logged_in_client, db_session, sample_user):
        thread = _make_thread(db_session, sample_user)
        _make_reply(db_session, thread, sample_user)
        body = logged_in_client.get(f'/board/{thread.id}').get_data(as_text=True)
        assert '保存修改' in body
