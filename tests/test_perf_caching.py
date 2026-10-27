"""性能相关回归：静态资源缓存头、图标/看板娘体积。"""
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent


def test_static_response_has_no_vary_cookie(client):
    """静态资源不能碰 session：带 Vary: Cookie 的话 Vercel 边缘会放弃缓存。"""
    resp = client.get('/static/css/style.css?v=v1')
    assert 'Cookie' not in resp.headers.get('Vary', '')
    assert 'Set-Cookie' not in resp.headers


def test_versioned_static_is_immutable(client):
    resp = client.get('/static/css/style.css?v=v9.9.9')
    assert resp.status_code == 200
    cache = resp.headers.get('Cache-Control', '')
    assert 'max-age=31536000' in cache
    assert 'immutable' in cache
    # 让 Vercel 边缘也缓存函数返回的静态资源
    assert resp.headers.get('Vercel-CDN-Cache-Control') == cache


def test_unversioned_image_cached_one_day(client):
    resp = client.get('/static/images/kanban-480.png')
    assert resp.status_code == 200
    cache = resp.headers.get('Cache-Control', '')
    assert 'max-age=86400' in cache
    assert 'immutable' not in cache


def test_sitemap_and_robots_cached(client):
    for path in ('/sitemap.xml', '/robots.txt', '/manifest.json'):
        resp = client.get(path)
        assert resp.status_code == 200
        assert 'max-age=3600' in resp.headers.get('Cache-Control', '')


def test_favicon_redirects_to_static(client):
    resp = client.get('/favicon.ico')
    assert resp.status_code == 302
    assert resp.headers['Location'].endswith('/static/favicon.ico')


def test_kanban_images_are_lightweight():
    webp = ROOT / 'static' / 'images' / 'kanban-480.webp'
    png = ROOT / 'static' / 'images' / 'kanban-480.png'
    assert webp.stat().st_size < 60_000, 'kanban WebP 应小于 60KB'
    assert png.stat().st_size < 160_000, 'kanban PNG 应小于 160KB'
    assert Image.open(webp).format == 'WEBP'
    assert Image.open(png).size == (480, 480)


def test_favicon_is_real_icon():
    ico = Image.open(ROOT / 'static' / 'favicon.ico')
    assert ico.format == 'ICO'


def test_templates_use_optimized_kanban():
    base = (ROOT / 'templates' / 'base.html').read_text(encoding='utf-8')
    assert 'kanban-480.webp' in base
    assert 'kanban-480.png' in base
    assert 'images/kanban.png' not in base
