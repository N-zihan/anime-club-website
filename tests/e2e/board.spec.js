import {expect, test} from '@playwright/test';

test.describe('留言板', () => {
    test.beforeEach(async ({page}) => {
        // 禁用新手引导（避免遮罩层拦截交互）
        await page.addInitScript(() => {
            for (let i = 0; i < 100; i++) {
                localStorage.setItem(`onboarding_done_${i}`, 'true');
            }
        });

        // 尝试注册（用户可能已存在，忽略错误）
        await page.goto('/register');
        await page.fill('input[name="username"]', 'testuser');
        await page.fill('input[name="qq"]', '123456789');
        await page.fill('input[name="email"]', 'test@qq.com');
        await page.fill('input[name="code"]', '123456');
        await page.fill('input[name="group"]', 'test_group_code');
        await page.fill('input[name="password"]', 'password123');
        await page.click('button[type="submit"]');
        // 无论注册是否成功，都去登录
        await page.goto('/login');
        await page.fill('input[name="username"]', 'testuser');
        await page.fill('input[name="password"]', 'password123');
        await page.click('button[type="submit"]');
        // 导航栏已移除用户名链接：登录成功判定改为「跳离登录页 + 抽屉个人中心显示用户名」
        await expect(page).not.toHaveURL(/\/login/);
        await expect(page.locator('.drawer-profile-name')).toHaveText('testuser');
    });

    test('留言板页面加载', async ({page}) => {
        await page.goto('/board');
        await expect(page.locator('h2')).toHaveText('社员留言板');
    });

    test('发表留言', async ({page}) => {
        await page.goto('/board');
        await page.fill('textarea[name="content"]', '这是一条测试留言');
        await page.click('.new-msg-submit');
        await expect(page.locator('.message-body')).toContainText('测试留言');
    });
});