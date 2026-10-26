"""public / public_contest 路由补充测试（覆盖率 55%~56% → 提升）。"""
from datetime import datetime, timedelta

from app.models import Candidate, Contest


class TestPublicPages:
    def test_settings_page(self, logged_in_client):
        resp = logged_in_client.get('/settings')
        assert resp.status_code == 200

    def test_about_history_activities_members(self, logged_in_client):
        for path in ('/about', '/about/history', '/activities', '/members', '/gallery'):
            assert logged_in_client.get(path).status_code == 200, path

    def test_404_custom_page(self, logged_in_client):
        resp = logged_in_client.get('/definitely-not-a-page')
        assert resp.status_code == 404
        assert '页面未找到' in resp.text or '404' in resp.text

    def test_robots_allows_static_images(self, client):
        # 微信/QQ 分享缩略图依赖爬虫能抓 /static/images/og-cover.png
        text = client.get('/robots.txt').get_data(as_text=True)
        assert 'Disallow: /\n' not in text          # 不能再全站禁抓
        assert 'Disallow: /admin' in text
        assert 'MicroMessenger' in text and 'QQ' in text
        # Sitemap 必须按请求域名动态拼接，不能写死线上域名
        assert 'Sitemap: http://localhost/sitemap.xml' in text

    def test_sitemap_is_generated_for_current_host(self, client):
        resp = client.get('/sitemap.xml')
        assert resp.status_code == 200
        assert 'xml' in resp.headers['Content-Type']
        text = resp.get_data(as_text=True)
        assert '<loc>http://localhost/</loc>' in text
        assert 'http://localhost/share' in text

        # 换一个 host 访问（模拟绑定自己的域名），loc 必须跟着变
        alt = client.get('/sitemap.xml', base_url='https://club.example.org').get_data(as_text=True)
        assert 'https://club.example.org/' in alt
        assert 'http://localhost' not in alt

    def test_static_assets_are_versioned(self, client):
        # 静态 JS/CSS 必须带 ?v= 版本号，否则 Service Worker / 浏览器会一直用旧脚本
        body = client.get('/login').get_data(as_text=True)
        assert 'style.css?v=' in body
        assert 'upload-compress.js?v=' in body

    def test_robots_and_manifest(self, client):
        robots = client.get('/robots.txt')
        assert robots.status_code == 200
        manifest = client.get('/manifest.json')
        assert manifest.status_code == 200 and manifest.is_json
        sw = client.get('/sw.js')
        assert sw.status_code == 200 and 'CACHE_VERSION' in sw.get_data(as_text=True)


class TestContestRules:
    def test_rules_page(self, logged_in_client, sample_contest):
        resp = logged_in_client.get(f'/contest/{sample_contest.id}/rules')
        assert resp.status_code == 200
        assert sample_contest.title in resp.text

    def test_rules_requires_login(self, client, sample_contest):
        assert client.get(f'/contest/{sample_contest.id}/rules').status_code == 302

    def test_missing_contest_404(self, logged_in_client):
        assert logged_in_client.get('/contest/999999/rules').status_code == 404


class TestNominationValidation:
    def test_nomination_requires_login(self, client, sample_contest):
        resp = client.post(f'/contest/{sample_contest.id}/nominate', data={})
        assert resp.status_code == 302

    def test_nomination_missing_contest(self, logged_in_client):
        resp = logged_in_client.post('/contest/999999/nominate', data={
            'name': 'x', 'source': 'y', 'gender': 'female'})
        assert resp.status_code == 404

    def test_nomination_empty_fields_rejected(self, logged_in_client, sample_contest):
        resp = logged_in_client.post(f'/contest/{sample_contest.id}/nominate',
                                     data={'name': '', 'source': '', 'gender': 'female'},
                                     follow_redirects=True)
        assert resp.status_code == 200  # 回到详情页并 flash 错误


class TestVoteGuards:
    def test_group_submit_outside_window_redirects(self, logged_in_client, db_session):
        now = datetime.now()
        contest = Contest(title='窗口外', description='x', type='saimoe',
                          gender_mode='separate', status='open',
                          open_at=now + timedelta(days=60), close_at=now + timedelta(days=90),
                          config={})
        db_session.add(contest)
        db_session.commit()
        resp = logged_in_client.post(f'/contest/{contest.id}/group/submit',
                                     data={'gender': 'female'})
        # 窗口外：拦截（302 回详情 / 200 提示）但不能 500
        assert resp.status_code in (200, 302)

    def test_knockout_submit_bad_contest(self, logged_in_client):
        resp = logged_in_client.post('/contest/999999/knockout/submit',
                                     data={'gender': 'female'})
        assert resp.status_code == 404

    def test_qualifying_submit_requires_login(self, client, sample_contest):
        resp = client.post(f'/contest/{sample_contest.id}/qualifying/submit',
                           data={'gender': 'female'})
        assert resp.status_code == 302


class TestAiEndpoints:
    def test_commentary_allowed_for_guest(self, client, sample_contest):
        # AI 解说接口在白名单里（匿名可看解说），不应被登录拦截
        resp = client.get(f'/contest/{sample_contest.id}/commentary')
        assert resp.status_code == 200 and resp.is_json

    def test_commentary_returns_json(self, logged_in_client, sample_contest):
        resp = logged_in_client.get(f'/contest/{sample_contest.id}/commentary?phase=nomination')
        assert resp.status_code == 200
        assert resp.is_json

    def test_predict_returns_json(self, logged_in_client, sample_contest):
        resp = logged_in_client.get(f'/contest/{sample_contest.id}/predict')
        # 同一赛事/IP 有冷却时间，全量跑时可能返回 429（也是 JSON）
        assert resp.status_code in (200, 429)
        assert resp.is_json


class TestVotesApi:
    def test_candidate_votes(self, logged_in_client, db_session, sample_contest):
        db_session.refresh(sample_contest)
        cand = sample_contest.candidates.first()
        if cand is None:
            candidate = Candidate(contest_id=sample_contest.id, name='投票角色',
                                  source='作品', gender='female', stage='pending')
            db_session.add(candidate)
            db_session.commit()
            cand = candidate
        resp = logged_in_client.get(f'/api/votes/{cand.id}')
        assert resp.status_code in (200, 404)
        if resp.status_code == 200:
            assert resp.is_json

class TestOpsHardening:
    """运维加固（v4.33.0）：健康检查 / 安全响应头 / PWA 图标。"""

    def test_health_endpoint(self, client):
        resp = client.get('/api/health')
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['ok'] is True and data['db'] == 'ok'
        assert data['version']
        assert data['time']

    def test_security_headers(self, client):
        resp = client.get('/login')
        headers = resp.headers
        assert headers.get('X-Content-Type-Options') == 'nosniff'
        assert headers.get('X-Frame-Options') == 'SAMEORIGIN'
        assert headers.get('Referrer-Policy') == 'strict-origin-when-cross-origin'

    def test_pwa_icons_are_real_png(self):
        from pathlib import Path
        from PIL import Image

        images = Path(__file__).resolve().parent.parent / 'static' / 'images'
        for name, size in (('icon-192.png', (192, 192)), ('icon-512.png', (512, 512)),
                           ('icon-512-maskable.png', (512, 512))):
            image = Image.open(images / name)
            assert image.format == 'PNG', f'{name} 不是真 PNG（历史 bug：曾把 JPEG 改名成 .png）'
            assert image.size == size, f'{name} 尺寸应为 {size}'

    def test_manifest_lists_sized_icons(self, client):
        data = client.get('/manifest.json').get_json()
        icons = {(icon['src'], icon['sizes'], icon['purpose']) for icon in data['icons']}
        assert ('/static/images/icon-192.png', '192x192', 'any') in icons
        assert ('/static/images/icon-512.png', '512x512', 'any') in icons
        assert ('/static/images/icon-512-maskable.png', '512x512', 'maskable') in icons

