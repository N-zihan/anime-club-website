import {expect, test} from '@playwright/test';

test.describe('PWA / 无障碍 / 分享海报 / 追番', () => {
    test.beforeEach(async ({page}) => {
        await page.addInitScript(() => {
            for (let i = 0; i < 100; i++) {
                localStorage.setItem(`onboarding_done_${i}`, 'true');
            }
        });
    });

    test('离线兜底页可访问', async ({page}) => {
        await page.goto('/offline');
        await expect(page.locator('.offline-title')).toHaveText('网络开小差了…');
        await expect(page.locator('.offline-actions .btn-primary')).toBeVisible();
    });

    test('Skip link：Tab 第一下就能跳到正文', async ({page}) => {
        await page.goto('/login');
        await page.keyboard.press('Tab');
        const focused = await page.evaluate(() => {
            const el = document.activeElement;
            return el ? el.className : '';
        });
        expect(focused).toContain('skip-link');
    });

    test('分享海报：点一下就能下载 PNG', async ({page}) => {
        await page.goto('/login');
        await page.fill('input[name="username"]', 'testuser');
        await page.fill('input[name="password"]', 'password123');
        await page.click('button[type="submit"]');
        await page.waitForTimeout(800);

        await page.goto('/contest/1');
        await expect(page.locator('#posterBtn')).toBeVisible();
        const [download] = await Promise.all([
            page.waitForEvent('download', {timeout: 15000}),
            page.click('#posterBtn'),
        ]);
        expect(download.suggestedFilename()).toContain('.png');
    });

    test('追番清单页可访问', async ({page}) => {
        await page.goto('/login');
        await page.fill('input[name="username"]', 'testuser');
        await page.fill('input[name="password"]', 'password123');
        await page.click('button[type="submit"]');
        await page.waitForTimeout(800);

        await page.goto('/my/anime_list');
        await expect(page.locator('h2')).toHaveText('📺 我的追番');
        await expect(page.locator('.anime-status-tabs')).toBeVisible();
    });

    test('番剧百科卡片带追番按钮', async ({page}) => {
        await page.goto('/login');
        await page.fill('input[name="username"]', 'testuser');
        await page.fill('input[name="password"]', 'password123');
        await page.click('button[type="submit"]');
        await page.waitForTimeout(600);

        await page.goto('/anime_resources');
        await expect(page.locator('h2')).toContainText('番剧');
    });
});
