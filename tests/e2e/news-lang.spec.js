import {expect, test} from '@playwright/test';

const NEWS_URL = 'http://localhost:5000/anime_news';
const SETTINGS_URL = 'http://localhost:5000/settings';
const PREF_KEY = 'anime_club_news_lang';

/**
 * 番剧新闻「显示语言」e2e（阶段 C：设置 → 语言与翻译）
 *
 * /api/anime/news 全部打桩：真实链路会抓上游并调用翻译（最高 12s 超时），
 * 会让用例又慢又不稳；翻译链路本身由 pytest 覆盖。
 */
const ARTICLES = [{
    title: 'Original Headline',
    title_zh: '中文标题',
    excerpt: 'Original summary',
    excerpt_zh: '中文摘要',
    link: 'https://example.com/news/1',
    source: 'ANN',
    date: '2026-10-20T00:00:00Z',
}];

async function stubNews(page, articles = ARTICLES) {
    await page.route('**/api/anime/news*', route => route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({articles, meta: {total: articles.length, hasMore: false, nextCursor: null}}),
    }));
}

test.describe('番剧新闻 · 显示语言偏好', () => {
    test.beforeEach(async ({page}) => {
        await stubNews(page);
    });

    test('默认译文优先，快捷切换按钮标记在「译文优先」', async ({page}) => {
        await page.goto(NEWS_URL);
        await expect(page.locator('.news-title a')).toHaveText(['中文标题']);
        await expect(page.locator('.news-excerpt')).toHaveText(['中文摘要']);
        await expect(page.locator('.news-lang-btn.is-active')).toHaveAttribute('data-lang', 'zh');
    });

    test('选「仅原文」后标题与摘要都变回原文，偏好写入本机并跨刷新保持', async ({page}) => {
        await page.goto(NEWS_URL);
        await page.locator('.news-lang-btn[data-lang="original"]').click();

        await expect(page.locator('.news-title a')).toHaveText(['Original Headline']);
        await expect(page.locator('.news-excerpt')).toHaveText(['Original summary']);
        expect(await page.evaluate(key => localStorage.getItem(key), PREF_KEY)).toBe('original');

        await page.reload();
        // 首帧就该带上属性（head 里的预绘制脚本），不能等 JS 渲染后再闪一下
        await expect(page.locator('html')).toHaveAttribute('data-news-lang', 'original');
        await expect(page.locator('.news-title a')).toHaveText(['Original Headline']);
    });

    test('选「双语对照」时中文标题在上、原文弱化在下', async ({page}) => {
        await page.goto(NEWS_URL);
        await page.locator('.news-lang-btn[data-lang="both"]').click();

        const first = page.locator('.news-title a').first();
        await expect(first).toContainText('中文标题');
        await expect(first.locator('.news-title-orig')).toHaveText('Original Headline');
        await expect(page.locator('.news-excerpt-orig')).toHaveText('Original summary');
    });

    test('设置页与新闻页共用同一偏好（改一边，另一边跟着变）', async ({page}) => {
        await page.goto(SETTINGS_URL);
        await page.locator('.pref-option[data-group="news-lang"][data-value="original"]').click();
        await expect(page.locator('html')).toHaveAttribute('data-news-lang', 'original');

        await page.goto(NEWS_URL);
        await expect(page.locator('.news-lang-btn.is-active')).toHaveAttribute('data-lang', 'original');
        await expect(page.locator('.news-title a')).toHaveText(['Original Headline']);

        // 反向：新闻页切回译文，设置页选项也应同步选中
        await page.locator('.news-lang-btn[data-lang="zh"]').click();
        await expect(page.locator('.news-title a')).toHaveText(['中文标题']);
        await page.goto(SETTINGS_URL);
        await expect(page.locator('.pref-option.is-active[data-group="news-lang"]')).toHaveAttribute('data-value', 'zh');
    });

    test('接口没有译文时回落到原文，列表不留空白', async ({page}) => {
        await page.unroute('**/api/anime/news*');
        await stubNews(page, [{
            title: 'Only Original Title',
            link: 'https://example.com/news/2',
            source: 'ANN',
            date: '2026-10-20T00:00:00Z',
        }]);

        await page.goto(NEWS_URL);
        await expect(page.locator('.news-title a')).toHaveText(['Only Original Title']);
        await expect(page.locator('.news-excerpt')).toHaveCount(0);
    });
});
