import {expect, test} from '@playwright/test';

test.describe('用户认证', () => {
    test('登录成功', async ({page}) => {
        await page.goto('/login');
        await page.fill('input[name="username"]', 'testuser');
        await page.fill('input[name="password"]', 'password123');
        await page.click('button[type="submit"]');
        // 导航栏已移除用户名链接：登录成功判定改为「跳离登录页 + 抽屉个人中心显示用户名」
        await expect(page).not.toHaveURL(/\/login/);
        await expect(page.locator('.drawer-profile-name')).toHaveText('testuser');
    });

    test('登录失败显示错误', async ({page}) => {
        await page.goto('/login');
        await page.fill('input[name="username"]', 'wronguser');
        await page.fill('input[name="password"]', 'wrongpass');
        await page.click('button[type="submit"]');
        await expect(page.locator('.flash.danger')).toBeVisible();
    });
});