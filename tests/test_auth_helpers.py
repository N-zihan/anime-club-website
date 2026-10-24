"""auth 模块辅助函数测试（覆盖率 52% → 目标 75%+）。"""
from unittest.mock import MagicMock, patch

from app import auth


class TestGroupCode:
    def test_valid(self):
        assert auth._validate_group_code('123456') is True  # pylint: disable=protected-access
        assert auth._validate_group_code(' 1234567 ') is True  # pylint: disable=protected-access

    def test_invalid(self):
        for code in (None, '', 'abc', '123', '12345678901234'):
            assert auth._validate_group_code(code) is False, code  # pylint: disable=protected-access


class TestVerificationCode:
    def test_generated_is_six_digits(self):
        for _ in range(20):
            code = auth._generate_verification_code()  # pylint: disable=protected-access
            assert len(code) == 6 and code.isdigit()

    def test_validate(self):
        assert auth._validate_verification_code('123456') is True  # pylint: disable=protected-access
        for code in (None, '', '12345', 'abcdef', 123456):
            assert auth._validate_verification_code(code) is False, code  # pylint: disable=protected-access


class TestSendEmail:
    def test_unconfigured_returns_false(self, monkeypatch):
        monkeypatch.setattr(auth, 'MAIL_USERNAME', None)
        monkeypatch.setattr(auth, 'MAIL_PASSWORD', None)
        assert auth.send_email('a@b.com', '主题', '正文') is False

    def test_success(self, monkeypatch):
        monkeypatch.setattr(auth, 'MAIL_USERNAME', 'sender@example.com')
        monkeypatch.setattr(auth, 'MAIL_PASSWORD', 'secret')
        server = MagicMock()
        monkeypatch.setattr(auth.smtplib, 'SMTP_SSL', lambda host, port: server)
        assert auth.send_email('a@b.com', '主题', '正文') is True
        server.login.assert_called_once()
        server.sendmail.assert_called_once()
        server.quit.assert_called_once()

    def test_failure_returns_false(self, monkeypatch):
        monkeypatch.setattr(auth, 'MAIL_USERNAME', 'sender@example.com')
        monkeypatch.setattr(auth, 'MAIL_PASSWORD', 'secret')
        monkeypatch.setattr(auth.smtplib, 'SMTP_SSL',
                            MagicMock(side_effect=RuntimeError('smtp down')))
        assert auth.send_email('a@b.com', '主题', '正文') is False

    def test_non_ssl_uses_starttls(self, monkeypatch):
        monkeypatch.setattr(auth, 'MAIL_USERNAME', 'sender@example.com')
        monkeypatch.setattr(auth, 'MAIL_PASSWORD', 'secret')
        monkeypatch.setattr(auth, 'SMTP_USE_SSL', False)
        server = MagicMock()
        monkeypatch.setattr(auth.smtplib, 'SMTP', lambda host, port: server)
        assert auth.send_email('a@b.com', '主题', '正文') is True
        server.starttls.assert_called_once()


class TestEmailWrappers:
    def test_verification_email_contains_code(self, monkeypatch):
        captured = {}

        def fake_send(to_email, subject, body, is_html=False):
            captured.update(to=to_email, subject=subject, body=body, html=is_html)
            return True

        monkeypatch.setattr(auth, 'send_email', fake_send)
        assert auth.send_verification_email('a@b.com', '654321') is True
        assert captured['to'] == 'a@b.com'
        assert '654321' in captured['body']
        assert captured['html'] is True

    def test_reset_email_contains_link(self, monkeypatch):
        captured = {}
        monkeypatch.setattr(auth, 'send_email',
                            lambda to, subject, body, is_html=False: captured.update(body=body) or True)
        assert auth.send_reset_email('a@b.com', 'https://x/reset/abc') is True
        assert 'reset/abc' in captured['body']

    def test_welcome_email_contains_username(self, monkeypatch):
        captured = {}
        monkeypatch.setattr(auth, 'send_email',
                            lambda to, subject, body, is_html=False: captured.update(body=body) or True)
        assert auth.send_welcome_email('a@b.com', '阿宅') is True
        assert '阿宅' in captured['body']


class TestEmailBatch:
    def test_unconfigured(self, monkeypatch):
        monkeypatch.setattr(auth, 'MAIL_USERNAME', None)
        monkeypatch.setattr(auth, 'MAIL_PASSWORD', None)
        assert auth.send_email_batch(['a@b.com'], '主题', '正文') == 0

    def test_empty_list(self):
        assert auth.send_email_batch([], '主题', '正文') == 0

    def test_success_counts(self, monkeypatch):
        monkeypatch.setattr(auth, 'MAIL_USERNAME', 'sender@example.com')
        monkeypatch.setattr(auth, 'MAIL_PASSWORD', 'secret')
        server = MagicMock()
        monkeypatch.setattr(auth.smtplib, 'SMTP_SSL', lambda host, port: server)
        assert auth.send_email_batch(['a@b.com', 'c@d.com'], '主题', '正文') == 2

    def test_partial_failure_continues(self, monkeypatch):
        monkeypatch.setattr(auth, 'MAIL_USERNAME', 'sender@example.com')
        monkeypatch.setattr(auth, 'MAIL_PASSWORD', 'secret')
        server = MagicMock()
        server.sendmail.side_effect = [None, RuntimeError('bad address'), None]
        monkeypatch.setattr(auth.smtplib, 'SMTP_SSL', lambda host, port: server)
        assert auth.send_email_batch(['a@b.com', 'bad@x', 'c@d.com'], '主题', '正文') == 2


class TestConfigHelpers:
    def test_csrf_config_default_enabled(self, monkeypatch):
        monkeypatch.delenv('DISABLE_CSRF_FOR_TESTS', raising=False)
        cfg = auth._get_csrf_config()  # pylint: disable=protected-access
        assert cfg['WTF_CSRF_ENABLED'] is True

    def test_csrf_config_disabled_by_env(self, monkeypatch):
        monkeypatch.setenv('DISABLE_CSRF_FOR_TESTS', '1')
        cfg = auth._get_csrf_config()  # pylint: disable=protected-access
        assert cfg['WTF_CSRF_ENABLED'] is False

    def test_session_cookie_config(self, app):
        auth._set_secure_session_cookie(app)  # pylint: disable=protected-access
        assert app.config['SESSION_COOKIE_HTTPONLY'] is True
        assert app.config['SESSION_COOKIE_SAMESITE'] == 'Lax'
        assert app.config['PERMANENT_SESSION_LIFETIME'].days == 7
