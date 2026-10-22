import {expect, test} from '@playwright/test';

test.describe('AI 好友', () => {
    test.beforeEach(async ({page}) => {
        // 禁用新手引导，避免遮罩拦截
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
        await expect(page.locator('.drawer-profile-name')).toHaveText('testuser');
    });

    test('AI 好友页加载', async ({page}) => {
        await page.goto('/ai_friend');
        await expect(page.locator('.ai-header h2')).toHaveText('🤖 AI 好友');
        await expect(page.locator('#ai-composer')).toBeVisible();
        await expect(page.locator('.ai-chat')).toBeVisible();
    });

    test('新建角色表单里的勾选框不会被撑宽（回归）', async ({page}) => {
        await page.goto('/ai_friend');
        await page.locator('.ai-create summary').click();
        const box = await page.locator('.ai-create input[type="checkbox"]').boundingBox();
        expect(box.width).toBeLessThan(40);
        expect(box.height).toBeLessThan(40);
        // 勾选框与文字在同一行，整行不能被拉成一条长条
        const rowBox = await page.locator('.ai-create .ai-check').boundingBox();
        expect(rowBox.width).toBeLessThan(320);
    });

    test('创建自定义角色后出现在「我的角色」', async ({page}) => {
        const name = `测试角色${Date.now() % 100000}`;
        await page.goto('/ai_friend');
        await page.locator('.ai-create summary').click();
        await page.fill('#pf-name', name);
        await page.fill('#pf-personality', '傲娇,认真');
        await page.fill('#pf-speech', '语气强硬，爱说哼');
        await page.fill('#pf-catch', '哼');
        await page.fill('#pf-address', '前辈');
        await page.locator('.ai-create button[type="submit"]').click();

        await expect(page.locator('.ai-persona-card').first()).toContainText(name);
        await expect(page.locator('#ai-persona-select option', {hasText: name})).toHaveCount(1);
    });

    test('发消息后显示 AI 回复（接口打桩）', async ({page}) => {
        await page.route('**/api/ai_friend/chat', route => route.fulfill({
            status: 200,
            contentType: 'application/json',
            body: JSON.stringify({
                ok: true, reply: '这是打桩的回复', fallback: false,
                model: 'fake-free-model', used: 2, limit: 30, notice: null,
            }),
        }));
        await page.goto('/ai_friend');
        await page.fill('#ai-input', '推荐一部治愈番');
        await page.locator('#ai-send').click();

        await expect(page.locator('.ai-msg-user .ai-bubble').last()).toContainText('推荐一部治愈番');
        await expect(page.locator('.ai-msg-bot .ai-bubble').last()).toContainText('这是打桩的回复');
        await expect(page.locator('#ai-used')).toHaveText('2');
    });

    test('额度用尽时显示提示（接口打桩）', async ({page}) => {
        await page.route('**/api/ai_friend/chat', route => route.fulfill({
            status: 200,
            contentType: 'application/json',
            body: JSON.stringify({
                ok: true, reply: '先看看常见问题：怎么参加萌战…', fallback: true,
                model: null, used: 30, limit: 30,
                notice: '今天的 AI 对话次数已用完（30 条/天），下面先按常见问题回答',
            }),
        }));
        await page.goto('/ai_friend');
        await page.fill('#ai-input', '还能聊吗');
        await page.locator('#ai-send').click();

        await expect(page.locator('#ai-notice')).toBeVisible();
        await expect(page.locator('#ai-notice')).toContainText('次数已用完');
    });

    test('「✨ 帮我补全」会填好人设字段（接口打桩）', async ({page}) => {
        await page.route('**/api/ai_friend/fill', route => route.fulfill({
            status: 200,
            contentType: 'application/json',
            body: JSON.stringify({
                ok: true,
                draft: {personality: '内向,认真', speech_style: '话少、轻声',
                        catchphrases: '……嗯', address_user: '前辈'},
            }),
        }));
        await page.goto('/ai_friend');
        await page.locator('.ai-create summary').click();
        await page.fill('#pf-name', '中野三玖');
        await page.locator('#ai-fill').click();

        await expect(page.locator('#pf-personality')).toHaveValue('内向,认真');
        await expect(page.locator('#pf-speech')).toHaveValue('话少、轻声');
        await expect(page.locator('#ai-fill-hint')).toContainText('请核对');
    });
});
