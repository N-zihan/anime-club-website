"""utils 模块补充测试（覆盖率 58% → 目标 80%+）。"""
import io
import ipaddress
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest
from PIL import Image

from app import utils


def _png_bytes(size=(1200, 800), mode='RGBA', color=(255, 0, 0, 128)):
    buf = io.BytesIO()
    Image.new(mode, size, color).save(buf, format='PNG')
    return buf.getvalue()


def _jpeg_bytes(size=(1200, 800)):
    buf = io.BytesIO()
    Image.new('RGB', size, (10, 20, 30)).save(buf, format='JPEG')
    return buf.getvalue()


class TestBlockedIp:
    def test_private_and_loopback_blocked(self):
        for ip in ('127.0.0.1', '10.0.0.1', '192.168.1.1', '169.254.169.254',
                   '0.0.0.0', '224.0.0.1', '100.100.100.200', '100.64.0.1'):
            assert utils._is_blocked_ip(ipaddress.ip_address(ip)) is True, ip  # pylint: disable=protected-access

    def test_public_allowed(self):
        for ip in ('8.8.8.8', '1.1.1.1'):
            assert utils._is_blocked_ip(ipaddress.ip_address(ip)) is False, ip  # pylint: disable=protected-access


class TestParseHttpUrl:
    def test_http_and_https_default_ports(self):
        assert utils._parse_http_url('http://example.com/a') == ('example.com', 80)  # pylint: disable=protected-access
        assert utils._parse_http_url('https://example.com') == ('example.com', 443)  # pylint: disable=protected-access

    def test_explicit_port(self):
        assert utils._parse_http_url('http://example.com:8080/x') == ('example.com', 8080)  # pylint: disable=protected-access

    def test_invalid(self):
        for url in ('ftp://example.com', 'example.com', '', 'http://:80/x', 'http://example.com:bad/x'):
            assert utils._parse_http_url(url) is None, url  # pylint: disable=protected-access


class TestIsPublicHttpUrl:
    def test_public_host(self, monkeypatch):
        monkeypatch.setattr(utils, 'resolve_public_ip', lambda host, port: '1.2.3.4')
        assert utils.is_public_http_url('https://example.com/x') is True

    def test_blocked_host(self, monkeypatch):
        monkeypatch.setattr(utils, 'resolve_public_ip', lambda host, port: None)
        assert utils.is_public_http_url('http://127.0.0.1/') is False

    def test_invalid_scheme(self):
        assert utils.is_public_http_url('file:///etc/passwd') is False


class TestResolvePublicIp:
    def test_public_resolves(self, monkeypatch):
        monkeypatch.setattr(utils.socket, 'getaddrinfo',
                            lambda *a, **kw: [(2, 1, 6, '', ('93.184.216.34', 443))])
        assert utils.resolve_public_ip('example.com', 443) == '93.184.216.34'

    def test_private_resolves_to_none(self, monkeypatch):
        monkeypatch.setattr(utils.socket, 'getaddrinfo',
                            lambda *a, **kw: [(2, 1, 6, '', ('127.0.0.1', 80))])
        assert utils.resolve_public_ip('localhost', 80) is None

    def test_dns_failure(self, monkeypatch):
        def boom(*a, **kw):
            raise utils.socket.gaierror('no dns')
        monkeypatch.setattr(utils.socket, 'getaddrinfo', boom)
        assert utils.resolve_public_ip('x', 80) is None


class TestCodeDigest:
    def test_digest_and_match(self, app):
        with app.app_context():
            digest = utils.code_digest('123456')
            assert digest and utils.code_matches(digest, '123456') is True
            assert utils.code_matches(digest, '654321') is False
            assert utils.code_matches('', '123456') is False
            assert utils.code_matches(digest, '') is False


class TestTimeHelpers:
    def test_cn_now_is_utc_plus_8(self):
        expected = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=8)
        actual = utils.cn_now()
        assert abs((actual - expected).total_seconds()) < 5

    def test_to_cn(self):
        base = datetime(2026, 1, 1, 0, 0, 0)
        assert utils.to_cn(base) == datetime(2026, 1, 1, 8, 0, 0)
        assert utils.to_cn(None) is None


class TestFileName:
    def test_allowed_file(self):
        assert utils.allowed_file('a.png') is True
        assert utils.allowed_file('a.JPG') is True
        assert utils.allowed_file('a.txt') is False
        assert utils.allowed_file('noext') is False

    def test_secure_filename(self):
        assert utils._secure_filename('../../etc/passwd.png') == 'passwd.png'  # pylint: disable=protected-access
        assert utils._secure_filename('a b.png') == 'a_b.png'  # pylint: disable=protected-access
        assert utils._secure_filename('.hidden') == 'hidden'  # pylint: disable=protected-access


class TestCompressImage:
    def test_rgba_png_becomes_jpeg_within_bounds(self):
        data = utils.compress_image(_png_bytes((1200, 800)))
        img = Image.open(io.BytesIO(data))
        assert img.format == 'JPEG'
        assert img.size[0] <= 800 and img.size[1] <= 800

    def test_png_output(self):
        data = utils.compress_image(_jpeg_bytes((400, 300)), output_format='PNG')
        assert Image.open(io.BytesIO(data)).format == 'PNG'


class TestGetOr404:
    def test_found(self, db_session, sample_user):
        assert utils.get_or_404(type(sample_user), sample_user.id) is sample_user

    def test_missing_aborts(self, db_session, sample_user):
        from app.models import Message  # pylint: disable=import-outside-toplevel
        with pytest.raises(Exception):
            utils.get_or_404(Message, 999999)


class TestValidateFileContent:
    def test_valid_png(self):
        assert utils._validate_file_content(_png_bytes(), 'a.png') is True  # pylint: disable=protected-access

    def test_valid_jpeg(self):
        assert utils._validate_file_content(_jpeg_bytes(), 'a.jpg') is True  # pylint: disable=protected-access

    def test_extension_mismatch(self):
        with pytest.raises(ValueError):
            utils._validate_file_content(_png_bytes(), 'a.jpg')  # pylint: disable=protected-access

    def test_bad_extension(self):
        with pytest.raises(ValueError):
            utils._validate_file_content(b'hello', 'a.exe')  # pylint: disable=protected-access

    def test_not_an_image(self):
        with pytest.raises(ValueError):
            utils._validate_file_content(b'not-image', 'a.png')  # pylint: disable=protected-access


class TestValidateFileUpload:
    def test_valid(self):
        result = utils.validate_file_upload(_png_bytes(), 'a.png')
        assert result['is_valid'] is True, result['errors']

    def test_empty_file(self):
        result = utils.validate_file_upload(b'', 'a.png')
        assert result['is_valid'] is False
        assert any('不能为空' in e for e in result['errors'])

    def test_too_large(self):
        result = utils.validate_file_upload(_png_bytes(), 'a.png', max_size_mb=0)
        assert result['is_valid'] is False
        assert any('文件大小超过限制' in e for e in result['errors'])

    def test_path_traversal(self):
        result = utils.validate_file_upload(_png_bytes(), '../a.png')
        assert result['is_valid'] is False
        assert any('路径遍历' in e or '文件名安全' in e for e in result['errors'])

    def test_wrong_type_content(self):
        result = utils.validate_file_upload(b'hello world', 'a.exe')
        assert result['is_valid'] is False
