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


    test('作者可以编辑自己的帖子和回复', async ({page}) => {
        const title = `可编辑主题-${Date.now()}`;
        await page.goto('/board/new');
        await page.fill('input[name="title"]', title);
        await page.fill('textarea[name="content"]', '原始正文');
        await page.click('.forum-form-actions button[type="submit"]');
        await expect(page.locator('.thread-title')).toHaveText(title);

        // 改主题
        await page.click('.thread-edit-link');
        await page.fill('input[name="title"]', title + '-改');
        await page.fill('textarea[name="content"]', '改过的正文');
        await page.click('.forum-form-actions button[type="submit"]');
        await expect(page.locator('.thread-title')).toHaveText(title + '-改');
        await expect(page.locator('.thread-body').first()).toContainText('改过的正文');
        await expect(page.locator('.thread-post-op .thread-edited')).toBeVisible();

        // 发一条回复再改
        await page.fill('#thread-reply-input', '原始回复');
        await page.click('.thread-composer-send');
        await expect(page.locator('.thread-posts')).toContainText('原始回复');
        await page.locator('.thread-posts .thread-reply-btn', {hasText: '编辑'}).last().click();
        const editForm = page.locator('.thread-reply-form[id^="edit-form-"]').last();
        await editForm.locator('textarea').fill('改过的回复');
        await editForm.locator('button[type="submit"]').click();
        await expect(page.locator('.thread-posts')).toContainText('改过的回复');
        await expect(page.locator('.thread-posts .thread-edited')).toHaveCount(1);
    });

    test('社员可以删除自己的回复和帖子', async ({page}) => {
        const title = `可删除主题-${Date.now()}`;
        await page.goto('/board/new');
        await page.fill('input[name="title"]', title);
        await page.fill('textarea[name="content"]', '待删除的正文');
        await page.click('.forum-form-actions button[type="submit"]');
        await expect(page.locator('.thread-title')).toHaveText(title);

        // 发一条回复并删除
        await page.fill('#thread-reply-input', '这条回复会被删除');
        await page.click('.thread-composer-send');
        await expect(page.locator('.thread-posts')).toContainText('这条回复会被删除');

        page.once('dialog', (dialog) => dialog.accept());
        await page.locator('.thread-posts .thread-delete-btn').last().click();
        // 删掉最后一条回复后，列表区变成空状态
        await expect(page.locator('.thread-empty')).toBeVisible();
        await expect(page.locator('body')).not.toContainText('这条回复会被删除');

        // 删除主题帖：回到论坛列表，且帖子消失
        page.once('dialog', (dialog) => dialog.accept());
        await page.locator('.thread-op-actions form button.thread-delete-btn').click();
        await expect(page).toHaveURL(/\/board$/);
        await expect(page.locator('.forum-row-title', {hasText: title})).toHaveCount(0);
    });

    test('窄屏下看板娘不遮挡发布/发送按钮，发送后输入框清空', async ({browser}) => {
        // 回归：之前看板娘在手机上盖住表单右下角按钮，点「发布」实际点到了立绘，
        // 表单没提交、输入框内容当然还在（表现为"发送后输入框未清空"）
        const ctx = await browser.newContext({ viewport: { width: 393, height: 851 } });
        const page = await ctx.newPage();
        await page.addInitScript(() => {
            for (let i = 0; i < 100; i++) {
                localStorage.setItem(`onboarding_done_${i}`, 'true');
            }
        });

        await page.goto('/login');
        await page.fill('input[name="username"]', 'testuser');
        await page.fill('input[name="password"]', 'password123');
        await page.click('button[type="submit"]');
        await expect(page).not.toHaveURL(/\/login/);

        const title = `窄屏发帖-${Date.now()}`;
        await page.goto('/board/new');
        await page.fill('input[name="title"]', title);
        await page.fill('textarea[name="content"]', '窄屏发帖正文');
        await page.click('.forum-form-actions button[type="submit"]');
        await expect(page.locator('.thread-title')).toHaveText(title);

        await page.fill('#thread-reply-input', '窄屏回复');
        await page.click('.thread-composer-send');
        await expect(page.locator('.thread-posts')).toContainText('窄屏回复');
        await expect(page.locator('#thread-reply-input')).toHaveValue('');

        await ctx.close();
    });
});
