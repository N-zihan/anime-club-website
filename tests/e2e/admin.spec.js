import {expect, test} from '@playwright/test';

test.describe('后台管理', () => {
    test.beforeEach(async ({page}) => {
        // 禁用新手引导（避免遮罩层拦截交互）
        await page.addInitScript(() => {
            for (let i = 0; i < 100; i++) {
                localStorage.setItem(`onboarding_done_${i}`, 'true');
            }
        });

        await page.goto('/login');
        await page.fill('input[name="username"]', 'testuser');
        await page.fill('input[name="password"]', 'password123');
        await page.click('button[type="submit"]');
        // 导航栏已移除用户名链接：登录成功判定改为「跳离登录页 + 抽屉个人中心显示用户名」
        await expect(page).not.toHaveURL(/\/login/);
        await expect(page.locator('.drawer-profile-name')).toHaveText('testuser');
    });

    test('站长面板加载', async ({page}) => {
        await page.goto('/admin/dashboard');
        await expect(page.locator('h2')).toHaveText('站长管理面板');
    });

    test('运营面板加载', async ({page}) => {
        await page.goto('/staff/dashboard');
        await expect(page.locator('h2')).toHaveText('运营管理面板');
    });

    test('活动管理加载', async ({page}) => {
        await page.goto('/admin/activities');
        await expect(page.locator('h2')).toHaveText('活动管理');
    });

    test('照片墙管理加载', async ({page}) => {
        await page.goto('/admin/gallery');
        await expect(page.locator('h2')).toHaveText('照片墙管理');
    });

    test('用户管理加载', async ({page}) => {
        await page.goto('/admin/users');
        await expect(page.locator('h2')).toHaveText('用户管理');
    });

    test('帖子管理加载', async ({page}) => {
        await page.goto('/admin/messages');
        await expect(page.locator('h2')).toHaveText('帖子管理');
    });

    test('操作日志页加载', async ({page}) => {
        await page.goto('/admin/logs');
        await expect(page.locator('h2')).toHaveText('🧾 操作日志');
        await expect(page.locator('.page-sub')).toContainText('共');
    });

    test('赛事管理加载', async ({page}) => {
        await page.goto('/admin/contests/manage');
        await expect(page.locator('h2')).toHaveText('赛事管理');
    });

    test('赛事编辑页加载', async ({page}) => {
        await page.goto('/admin/contests/edit/1');
        await expect(page.locator('h2')).toContainText('编辑赛事');
    });
});