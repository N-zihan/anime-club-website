import {expect, test} from '@playwright/test';

test.describe.skip('投票流程', () => {
    test.beforeEach(async ({page}) => {
        await page.goto('/login');
        await page.fill('input[name="username"]', 'testuser');
        await page.fill('input[name="password"]', 'password123');
        await page.click('button[type="submit"]');
        // 导航栏已移除用户名链接：登录成功判定改为「跳离登录页 + 抽屉个人中心显示用户名」
        await expect(page).not.toHaveURL(/\/login/);
        await expect(page.locator('.drawer-profile-name')).toHaveText('testuser');
    });

    test('海选投票页面加载', async ({page}) => {
        await page.goto('/contest/1/qualifying/female');
        await expect(page.locator('h2')).toContainText('海选投票');
    });

    test('海选投票加减票', async ({page}) => {
        await page.goto('/contest/1/qualifying/female');
        await page.click('.vote-btn[data-action="increase"]');
        await expect(page.locator('.vote-count')).toHaveText('1');
        await page.click('.vote-btn[data-action="decrease"]');
        await expect(page.locator('.vote-count')).toHaveText('0');
    });

    test('小组赛投票页面加载', async ({page}) => {
        await page.goto('/contest/1/group/female');
        await expect(page.locator('h2')).toContainText('小组赛');
    });

    test('淘汰赛投票页面加载', async ({page}) => {
        await page.goto('/contest/1/knockout/female');
        await expect(page.locator('h2')).toContainText('淘汰赛');
    });
});