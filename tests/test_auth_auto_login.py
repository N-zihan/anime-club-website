"""自动登录（记住我）测试。"""
from app.models import User


def test_login_page_shows_remember_checkbox(client):
    body = client.get('/login').get_data(as_text=True)
    assert '自动登录' in body
    assert 'name="remember"' in body


def test_login_with_remember_sets_persistent_cookie(client, sample_user):
    resp = client.post('/login', data={
        'username': sample_user.username,
        'password': 'password123',
        'remember': '1',
    })
    assert resp.status_code == 302
    cookie = resp.headers.get('Set-Cookie', '')
    assert 'Expires=' in cookie or 'Max-Age=' in cookie


def test_login_without_remember_is_session_cookie(client, sample_user):
    resp = client.post('/login', data={
        'username': sample_user.username,
        'password': 'password123',
    })
    assert resp.status_code == 302
    cookie = resp.headers.get('Set-Cookie', '')
    assert 'Expires=' not in cookie
    assert 'Max-Age=' not in cookie
