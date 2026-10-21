import {expect, test} from '@playwright/test';

test.describe('论坛（原留言板）', () => {
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
        await expect(page).not.toHaveURL(/\/login/);
        await expect(page.locator('.drawer-profile-name')).toHaveText('testuser');
    });

    test('论坛首页加载', async ({page}) => {
        await page.goto('/board');
        await expect(page.locator('h2')).toHaveText('动漫社论坛');
        await expect(page.locator('.forum-new-btn')).toBeVisible();
    });

    test('发帖后进入帖子页，底部输入框固定且可直接回复', async ({page}) => {
        const title = `测试主题-${Date.now()}`;
        const body = '这是通过论坛发帖表单提交的正文';

        await page.goto('/board/new');
        await page.fill('input[name="title"]', title);
        await page.fill('textarea[name="content"]', body);
        await page.click('.forum-form-actions button[type="submit"]');

        await expect(page.locator('.thread-title')).toHaveText(title);
        await expect(page.locator('.thread-body').first()).toContainText(body);

        // 底部输入框始终贴在视口底部（无需滚到底才能打字）
        const composer = page.locator('.thread-composer');
        await expect(composer).toBeVisible();
        const box = await composer.boundingBox();
        const viewport = page.viewportSize();
        expect(box.y + box.height).toBeGreaterThan(viewport.height - 12);

        // 页面在顶部时直接输入并发送
        await page.fill('#thread-reply-input', '从固定输入框发出的回复');
        await page.click('.thread-composer-send');
        await expect(page.locator('.thread-posts')).toContainText('从固定输入框发出的回复');

        // 刷新后回复仍在，且输入框依旧固定
        await page.reload();
        await expect(page.locator('.thread-posts')).toContainText('从固定输入框发出的回复');
        const box2 = await page.locator('.thread-composer').boundingBox();
        expect(box2.y + box2.height).toBeGreaterThan(viewport.height - 12);
    });

    test('新帖列表里能看到刚发的主题', async ({page}) => {
        const title = `列表可见性-${Date.now()}`;
        await page.goto('/board/new');
        await page.fill('input[name="title"]', title);
        await page.fill('textarea[name="content"]', '列表可见性测试正文');
        await page.click('.forum-form-actions button[type="submit"]');

        await page.goto('/board');
        await expect(page.locator('.forum-row-title').first()).toHaveText(title);
    });

    test('访客访问论坛会被引导登录', async ({page, browser}) => {
        // 用全新上下文（无登录态）：论坛仍要求登录后查看
        const ctx = await browser.newContext();
        const guest = await ctx.newPage();
        await guest.goto('/board');
        await expect(guest).toHaveURL(/\/login/);
        await ctx.close();
    });
});
