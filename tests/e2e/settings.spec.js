import {expect, test} from '@playwright/test';

const SETTINGS_URL = 'http://localhost:5000/settings';
const SH_KEY = 'anime_news_search_history_v1';

/**
 * 设置页 e2e：外观与显示 / 数据与隐私（阶段 A）
 *
 * 说明：偏好全部存本机 localStorage，访客即可使用，因此用例不需要登录
 * （登录后 base.html 还会渲染新手引导遮罩，会把点击拦住）。
 */
test.describe('设置页 · 外观与显示', () => {
    test.beforeEach(async ({page}) => {
        await page.goto(SETTINGS_URL);
    });

    test('访客可直接打开，分区与控件齐全', async ({page}) => {
        await expect(page.locator('h1.settings-title')).toBeVisible();
        // 访客：外观 + 语言与翻译 + 通知（登录提示）+ 数据，共四个分区
        await expect(page.locator('.settings-section')).toHaveCount(4);
        await expect(page.locator('.pref-option[data-group="theme"]')).toHaveCount(3);
        await expect(page.locator('.pref-option[data-group="font"]')).toHaveCount(3);
        await expect(page.locator('.pref-option[data-group="motion"]')).toHaveCount(2);
        await expect(page.locator('.pref-option[data-group="news-lang"]')).toHaveCount(3);
        // 当前偏好应被标记为选中
        await expect(page.locator('.pref-option.is-active[data-group="theme"]')).toHaveCount(1);
    });

    test('访客看不到通知设置表单，只看到登录提示', async ({page}) => {
        await expect(page.locator('.settings-login-hint')).toBeVisible();
        await expect(page.locator('input[name="muted_types"]')).toHaveCount(0);
    });

    test('深色主题立即生效并持久化，刷新后仍是深色', async ({page}) => {
        await page.locator('.pref-option[data-group="theme"][data-value="dark"]').click();
        await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');
        await expect(page.locator('.pref-option[data-group="theme"][data-value="dark"]')).toHaveClass(/is-active/);
        expect(await page.evaluate(() => localStorage.getItem('anime_club_theme'))).toBe('dark');

        await page.reload();
        await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');
    });

    test('「跟随系统」随系统偏好变化（emulateMedia 切换）', async ({page}) => {
        await page.emulateMedia({colorScheme: 'dark'});
        await page.locator('.pref-option[data-group="theme"][data-value="system"]').click();
        await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');

        await page.emulateMedia({colorScheme: 'light'});
        await expect(page.locator('html')).not.toHaveAttribute('data-theme', 'dark');
        expect(await page.evaluate(() => localStorage.getItem('anime_club_theme'))).toBe('system');
    });

    test('字号调大后根字号与正文实际变大', async ({page}) => {
        const before = await page.evaluate(() => getComputedStyle(document.documentElement).fontSize);
        await page.locator('.pref-option[data-group="font"][data-value="large"]').click();
        await expect(page.locator('html')).toHaveAttribute('data-font', 'large');

        const after = await page.evaluate(() => ({
            root: getComputedStyle(document.documentElement).fontSize,
            body: getComputedStyle(document.body).fontSize,
        }));
        expect(parseFloat(after.root)).toBeGreaterThan(parseFloat(before));
        expect(parseFloat(after.body)).toBeGreaterThan(parseFloat(before));

        // 刷新后仍然生效（避免"设置完就丢"）
        await page.reload();
        await expect(page.locator('html')).toHaveAttribute('data-font', 'large');
    });

    test('减少动效后过渡时长几乎归零', async ({page}) => {
        await page.locator('.pref-option[data-group="motion"][data-value="reduced"]').click();
        await expect(page.locator('html')).toHaveAttribute('data-motion', 'reduced');

        const seconds = await page.evaluate(() => {
            const el = document.querySelector('.settings-section');
            return parseFloat(getComputedStyle(el).transitionDuration) || 0;
        });
        expect(seconds).toBeLessThanOrEqual(0.01);
    });

    test('导航栏昼夜按钮仍可快速切换', async ({page}) => {
        await page.locator('.pref-option[data-group="theme"][data-value="light"]').click();
        await expect(page.locator('html')).not.toHaveAttribute('data-theme', 'dark');

        await page.locator('#theme-toggle').click();
        await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');
        expect(await page.evaluate(() => localStorage.getItem('anime_club_theme'))).toBe('dark');
    });
});

test.describe('设置页 · 数据与隐私', () => {
    test('清除搜索历史后计数归零且本地键被删除', async ({page}) => {
        await page.goto(SETTINGS_URL);
        await page.evaluate(key => {
            localStorage.setItem(key, JSON.stringify([{q: '药屋少女'}, {q: '海贼王'}]));
        }, SH_KEY);
        await page.reload();

        await expect(page.locator('#sh-count')).toHaveText('2');
        await page.locator('#sh-clear').click();
        await expect(page.locator('#sh-count')).toHaveText('0');
        await expect(page.locator('#settings-toast')).toHaveClass(/is-visible/);
        expect(await page.evaluate(key => localStorage.getItem(key), SH_KEY)).toBeNull();
    });

    test('重新观看引导：清除 onboarding 标记', async ({page}) => {
        await page.goto(SETTINGS_URL);
        await page.evaluate(() => localStorage.setItem('onboarding_done_7', '1'));
        await page.reload();

        await page.locator('#onboarding-reset').click();
        expect(await page.evaluate(() => localStorage.getItem('onboarding_done_7'))).toBeNull();
    });

    test('清空本地设置后所有 anime_club_* 与引导标记都被移除', async ({page}) => {
        await page.goto(SETTINGS_URL);
        await page.evaluate(() => {
            localStorage.setItem('anime_club_theme', 'dark');
            localStorage.setItem('anime_club_font', 'large');
            localStorage.setItem('anime_club_motion', 'reduced');
            localStorage.setItem('onboarding_done_9', '1');
        });

        page.once('dialog', d => d.accept());
        await page.locator('#reset-all').click();
        await page.waitForLoadState('load');

        const left = await page.evaluate(() => Object.keys(localStorage).filter(
            k => k.indexOf('anime_club_') === 0 || k.indexOf('onboarding_done_') === 0
        ));
        expect(left).toEqual([]);
        await expect(page.locator('html')).not.toHaveAttribute('data-theme', 'dark');
    });
});

test.describe('设置页 · 通知与免打扰（登录后）', () => {
    test.beforeEach(async ({page}) => {
        await page.goto('/login');
        await page.fill('input[name="username"]', 'testuser');
        await page.fill('input[name="password"]', 'password123');
        await page.click('button[type="submit"]');
        await expect(page).not.toHaveURL(/\/login/);

        await page.goto(SETTINGS_URL);
        // 登录后会有新手引导遮罩，先按「跳过」再操作设置
        const skip = page.locator('#onboarding-skip');
        if (await skip.isVisible().catch(() => false)) {
            await skip.click();
        }
    });

    test('可勾选静音类型并保存到账号（换页仍生效）', async ({page}) => {
        await expect(page.locator('input[name="muted_types"]')).toHaveCount(3);
        await expect(page.locator('.settings-switch input[name="email_enabled"]')).toHaveCount(1);
        await expect(page.locator('.settings-switch input[name="badge_enabled"]')).toHaveCount(1);
        await expect(page.locator('select[name="badge_poll_seconds"] option')).toHaveCount(3);

        await page.locator('input[name="muted_types"][value="audit"]').check();
        await page.locator('select[name="badge_poll_seconds"]').selectOption('120');
        await page.locator('.settings-form-actions button[type="submit"]').click();

        // 保存后回到设置页并给出提示
        await expect(page).toHaveURL(/\/settings/);
        await expect(page.locator('.flash')).toBeVisible();

        await page.goto(SETTINGS_URL);
        await expect(page.locator('input[name="muted_types"][value="audit"]')).toBeChecked();
        await expect(page.locator('input[name="muted_types"][value="reply"]')).not.toBeChecked();
        await expect(page.locator('select[name="badge_poll_seconds"]')).toHaveValue('120');
    });

    test('关闭未读红点时不再轮询 /api/notifications/count', async ({page}) => {
        const polls = [];
        page.on('request', r => {
            if (r.url().includes('/api/notifications/count')) polls.push(r.url());
        });

        // 先确认默认是轮询的
        await page.goto('/home');
        await expect.poll(() => polls.length).toBeGreaterThan(0);

        await page.goto(SETTINGS_URL);
        const toggle = page.locator('.settings-switch input[name="badge_enabled"]');
        if (await toggle.isChecked()) await toggle.uncheck();
        await page.locator('.settings-form-actions button[type="submit"]').click();
        await expect(page).toHaveURL(/\/settings/);

        // 重新加载设置页：此时它已按「不显示红点」渲染，不会再发轮询请求；
        // 先等一会儿消化掉上一页可能残留的请求事件，再清零计数，
        // 否则上一条在途请求会让下面的断言假失败（曾复现过一次）。
        await page.goto(SETTINGS_URL);
        await expect(toggle).not.toBeChecked();
        await page.waitForTimeout(800);
        polls.length = 0;

        await page.goto('/home');
        await page.waitForTimeout(1500);
        expect(polls).toEqual([]);
    });
});

test.describe('设置页 · 入口与布局', () => {
    test('抽屉内有「设置」入口并可跳转', async ({page}) => {
        await page.goto('/home');
        await page.locator('#menu-toggle').click();
        await expect(page.locator('#drawer')).toHaveClass(/open/);

        const entry = page.locator('#drawer a[href="/settings"]');
        await expect(entry).toBeVisible();
        await expect(entry).toHaveText(/设置/);

        await entry.click();
        await expect(page).toHaveURL(/\/settings/);
        await expect(page.locator('h1.settings-title')).toBeVisible();
    });

    test('无横向溢出，触控目标不小于 40px', async ({page}) => {
        await page.goto(SETTINGS_URL);
        const overflow = await page.evaluate(() => ({
            scrollWidth: document.documentElement.scrollWidth,
            innerWidth: window.innerWidth,
        }));
        expect(overflow.scrollWidth).toBeLessThanOrEqual(overflow.innerWidth + 1);

        const tooSmall = await page.evaluate(() => {
            const bad = [];
            document.querySelectorAll('.pref-option, .settings-row .btn').forEach(el => {
                const r = el.getBoundingClientRect();
                if (r.height < 40) bad.push(el.textContent.trim() + ':' + Math.round(r.height));
            });
            return bad;
        });
        expect(tooSmall).toEqual([]);
    });
});
