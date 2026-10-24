"""公开分享落地页测试：微信/QQ 爬虫能拿到标题与首图。"""
from datetime import datetime, timedelta

from app.models import Contest


class TestSharePages:
    def test_share_home_public(self, client):
        resp = client.get('/share')
        assert resp.status_code == 200
        body = resp.get_data(as_text=True)
        assert 'og:image' in body
        assert 'og:image:type' in body
        assert 'share-hero' in body  # 可见首图（微信缩略图来源）

    def test_share_contest_public(self, client, sample_contest):
        resp = client.get(f'/share/contest/{sample_contest.id}')
        assert resp.status_code == 200
        body = resp.get_data(as_text=True)
        assert sample_contest.title in body
        assert 'og:title' in body and sample_contest.title in body
        assert 'share-hero' in body
        assert '登录查看完整赛事' in body

    def test_share_contest_missing_404(self, client, db_session):
        assert client.get('/share/contest/999999').status_code == 404

    def test_share_contest_shows_phase_and_counts(self, client, db_session):
        now = datetime.now()
        contest = Contest(title='分享测试赛', description='说明文字', type='saimoe',
                          gender_mode='separate', status='open',
                          open_at=now - timedelta(days=1), close_at=now + timedelta(days=30),
                          config={})
        db_session.add(contest)
        db_session.commit()
        body = client.get(f'/share/contest/{contest.id}').get_data(as_text=True)
        assert '位参赛角色' in body

    def test_splash_has_hidden_crawlable_image(self, client):
        # 封面页是 canvas 动画，微信不跑 JS 也不读 og；必须有一张真实 <img> 给爬虫抓，
        # 但视觉上要藏出屏幕（用户要求「要藏，但要能被爬虫抓到」）
        body = client.get('/').get_data(as_text=True)
        assert 'wechat-thumb' in body
        assert 'images/og-cover.png' in body
        assert 'left:-9999px' in body   # 不可见、不占版面，只给微信爬虫抓首图
        assert 'width="1200" height="630"' in body  # 微信要求首图尺寸 >= 300x300
        # QQ / QQ 空间读 og:image，封面页也得有
        assert 'property="og:image"' in body
        assert 'static/images/og-cover.png' in body

    def test_og_image_has_cache_version(self, client):
        body = client.get('/share').get_data(as_text=True)
        assert 'og-cover.png?v=' in body

    def test_contest_detail_has_share_button(self, logged_in_client, sample_contest):
        body = logged_in_client.get(f'/contest/{sample_contest.id}').get_data(as_text=True)
        assert 'shareLinkBtn' in body
        assert f'share/contest/{sample_contest.id}' in body

    def test_contest_detail_has_no_leaked_js_text(self, logged_in_client, sample_contest):
        """回归：分享链接那段 JS 曾落在 <script> 外面，被浏览器当纯文本显示在页面上。"""
        from bs4 import BeautifulSoup

        body = logged_in_client.get(f'/contest/{sample_contest.id}').get_data(as_text=True)
        soup = BeautifulSoup(body, 'html.parser')
        for tag in soup.find_all(['script', 'style']):
            tag.decompose()
        visible = soup.get_text(' ', strip=True)
        assert 'navigator.clipboard' not in visible
        assert 'document.getElementById' not in visible
        assert 'addEventListener' not in visible
