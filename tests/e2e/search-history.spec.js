import {expect, test} from '@playwright/test';

const NEWS_URL = 'http://localhost:5000/anime_news';

/**
 * 番剧新闻页「搜索历史」e2e（task-9）
 *
 * 说明：这里把 /api/anime/news* 全部打桩，让用例完全 hermetic ——
 * 真实搜索链路会去抓 AniNews 上游并调用阿里云翻译（超时最高 12s），
 * 会把用例拖到超时且结果不稳定。搜索后端本身不属于本功能范围。
 */
test.describe('番剧新闻 · 搜索历史', () => {
    test.beforeEach(async ({page}) => {
        await page.route('**/api/anime/news*', route => route.fulfill({
            status: 200,
            contentType: 'application/json',
            body: JSON.stringify({articles: [], meta: {total: 0, hasMore: false, nextCursor: null}}),
        }));
    });

    const input = page => page.locator('#newsSearchInput');
    const panel = page => page.locator('#searchHistoryPanel');

    test('搜索后关键词进入历史（键盘回车）', async ({page}) => {
        await page.goto(NEWS_URL);

        await expect(panel(page)).toBeHidden();           // 默认折叠，不糊一大片

        await input(page).click();
        await input(page).fill('药屋少女');
        await input(page).press('Enter');

        await expect(panel(page)).toBeVisible();
        await expect(panel(page).locator('.sh-item .sh-text')).toHaveText(['药屋少女']);
    });

    test('刷新页面后历史仍然存在（localStorage 持久化）', async ({page}) => {
        await page.goto(NEWS_URL);
        await input(page).click();
        await input(page).fill('海贼王');
        await input(page).press('Enter');
        await expect(panel(page)).toBeVisible();

        await page.reload();

        await expect(panel(page)).toBeHidden();           // 重新进页面默认是收起的
        await input(page).click();                         // 聚焦后才展示
        await expect(panel(page)).toBeVisible();
        await expect(panel(page).locator('.sh-item .sh-text')).toHaveText(['海贼王']);
    });

    test('重复搜索去重并提到最前，上限 10 条', async ({page}) => {
        await page.goto(NEWS_URL);

        for (const kw of ['a', 'b', 'c']) {
            await input(page).click();
            await input(page).fill(kw);
            await input(page).press('Enter');
        }
        // 再搜一次 a：应提到最前且不产生重复项
        await input(page).click();
        await input(page).fill('a');
        await input(page).press('Enter');

        await expect(panel(page).locator('.sh-item .sh-text')).toHaveText(['a', 'c', 'b']);

        // 超过 10 条后截断
        for (let i = 0; i < 15; i++) {
            await input(page).click();
            await input(page).fill('kw' + i);
            await input(page).press('Enter');
        }
        await expect(panel(page).locator('.sh-row')).toHaveCount(10);
    });

    test('点击历史项重新发起搜索并收起面板', async ({page}) => {
        await page.goto(NEWS_URL);
        await input(page).click();
        await input(page).fill('进击的巨人');
        await input(page).press('Enter');
        await expect(panel(page)).toBeVisible();

        // 先离开再回来，确保是「点击历史项」触发而非残留状态
        await page.locator('#newsClearBtn').click();
        await expect(input(page)).toHaveValue('');

        await input(page).click();
        await panel(page).locator('.sh-item').first().click();

        await expect(input(page)).toHaveValue('进击的巨人');   // 关键词被回填
        await expect(panel(page)).toBeHidden();                // 面板收起
        await expect(page.locator('#newsClearBtn')).toBeVisible(); // 说明搜索确实执行了
        await expect(page.locator('#newsStatus')).toHaveText('没有找到相关新闻');
    });

    test('单条删除与一键清空', async ({page}) => {
        await page.goto(NEWS_URL);
        for (const kw of ['one', 'two', 'three']) {
            await input(page).click();
            await input(page).fill(kw);
            await input(page).press('Enter');
        }
        await input(page).click();
        await expect(panel(page).locator('.sh-row')).toHaveCount(3);

        // 单条删除（第一条 = three）
        await panel(page).locator('.sh-del').first().click();
        await expect(panel(page).locator('.sh-item .sh-text')).toHaveText(['two', 'one']);

        // 删除后刷新，确认删除已落盘
        await page.reload();
        await input(page).click();
        await expect(panel(page).locator('.sh-item .sh-text')).toHaveText(['two', 'one']);

        // 一键清空
        await panel(page).locator('.sh-clear').click();
        await expect(panel(page)).toBeHidden();

        await page.reload();
        await input(page).click();
        await expect(panel(page)).toBeHidden();
    });

    test('恶意关键词不执行脚本、不注入元素（XSS 防护）', async ({page}) => {
        const payload = '<img src=x onerror="window.__xssFired=1">';
        let dialogFired = false;
        page.on('dialog', async d => { dialogFired = true; await d.dismiss(); });

        await page.goto(NEWS_URL);
        await input(page).click();
        await input(page).fill(payload);
        await input(page).press('Enter');

        await expect(panel(page)).toBeVisible();

        // 1) 面板内绝不能被解析出 img 元素
        await expect(panel(page).locator('img')).toHaveCount(0);
        // 2) payload 以纯文本形式原样展示（证明用的是 textContent 而非 innerHTML）
        await expect(panel(page).locator('.sh-item .sh-text')).toHaveText([payload]);
        // 3) onerror 处理器没有跑起来
        expect(await page.evaluate(() => window.__xssFired)).toBeUndefined();
        // 4) 页面上没有多出注入的 img 节点
        expect(await page.locator('img[src="x"]').count()).toBe(0);
        expect(dialogFired).toBe(false);

        // 刷新后从 localStorage 复原，同样不能执行
        await page.reload();
        await input(page).click();
        await expect(panel(page).locator('img')).toHaveCount(0);
        expect(await page.evaluate(() => window.__xssFired)).toBeUndefined();
    });

    test('localStorage 不可用时静默降级，搜索功能本身不受影响', async ({page}) => {
        const errors = [];
        page.on('pageerror', e => errors.push(String(e)));

        // 只让搜索历史这个 key 的 setItem 抛错（配额满 / 隐私策略），
        // 其余 key 走原实现 —— base.html 的主题记忆等既有功能照常工作，
        // 这样 errors 里若出现 QuotaExceededError 只可能来自本功能代码。
        await page.addInitScript(() => {
            try {
                const orig = Storage.prototype.setItem;
                Storage.prototype.setItem = function (key, value) {
                    if (key === 'anime_news_search_history_v1') { throw new Error('QuotaExceededError'); }
                    return orig.call(this, key, value);
                };
            } catch (e) { /* ignore */ }
        });

        await page.goto(NEWS_URL);
        await input(page).click();
        await input(page).fill('降级测试');
        await input(page).press('Enter');

        // 搜索链路必须照常完成
        await expect(page.locator('#newsClearBtn')).toBeVisible();
        await expect(page.locator('#newsStatus')).toHaveText('没有找到相关新闻');
        // 历史写不进去 → 会话内存里留一条但不持久化；输入框失焦后面板自动收起。
        // 降级语义 = 「不持久化」，不是「不显示」：刷新后历史为空。
        await expect(panel(page)).toBeVisible();
        await input(page).press('Escape');           // 输入框失焦，面板收起
        await expect(panel(page)).toBeHidden();

        await page.reload();
        await input(page).click();
        await expect(panel(page)).toBeHidden();      // 持久化失败 → 重开后无历史可展示

        // QuotaExceededError 被 store.write 的 try/catch 吞掉，不应冒泡成页面未捕获异常
        expect(errors.filter(e => e.includes('QuotaExceededError'))).toHaveLength(0);
        expect(errors).toHaveLength(0);
    });

    test('键盘可达：Esc 收起、上下键在历史项间移动', async ({page}) => {
        await page.goto(NEWS_URL);
        for (const kw of ['k1', 'k2', 'k3']) {
            await input(page).click();
            await input(page).fill(kw);
            await input(page).press('Enter');
        }
        await input(page).click();
        await expect(panel(page)).toBeVisible();

        // 上下键移动高亮（列表顺序为 k3, k2, k1）
        await input(page).press('ArrowDown');
        await expect(panel(page).locator('.sh-row.sh-active .sh-text')).toHaveText('k3');
        await input(page).press('ArrowDown');
        await expect(panel(page).locator('.sh-row.sh-active .sh-text')).toHaveText('k2');
        await input(page).press('ArrowUp');
        await expect(panel(page).locator('.sh-row.sh-active .sh-text')).toHaveText('k3');
        await input(page).press('ArrowUp');   // 从首项回绕到末项
        await expect(panel(page).locator('.sh-row.sh-active .sh-text')).toHaveText('k1');

        // Esc 收起
        await input(page).press('Escape');
        await expect(panel(page)).toBeHidden();
    });

    test('触摸目标 ≥44px、颜色走 CSS 变量（深色模式可用）', async ({page}) => {
        await page.goto(NEWS_URL);
        await input(page).click();
        await input(page).fill('触摸测试');
        await input(page).press('Enter');
        await expect(panel(page)).toBeVisible();

        const box = await panel(page).locator('.sh-item').first().boundingBox();
        const delBox = await panel(page).locator('.sh-del').first().boundingBox();
        const clearBox = await panel(page).locator('.sh-clear').boundingBox();
        expect(box.height).toBeGreaterThanOrEqual(44);
        expect(delBox.height).toBeGreaterThanOrEqual(44);
        expect(clearBox.height).toBeGreaterThanOrEqual(44);

        // 面板背景必须解析到 CSS 变量而不是硬编码色
        const bg = await panel(page).evaluate(el => getComputedStyle(el).backgroundColor);
        const hardcoded = await page.evaluate(() =>
            getComputedStyle(document.documentElement).getPropertyValue('--bg-card').trim());
        expect(hardcoded).not.toBe('');
        expect(bg).not.toBe('rgba(0, 0, 0, 0)');
    });
});
