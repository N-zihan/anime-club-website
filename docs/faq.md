# 常见问题

按场景分类，点开对应问题看答案。如果这里没覆盖到，去看 [开发约定与踩坑](dev-notes.md) 或直接提 Issue。

## 访问与账号

??? question "为什么我打开 /home、/board 全被跳到登录页？"
    这是**设计如此**。站点是社团内部站，`app/__init__.py` 里的全局守卫会把未登录用户拦到 `/login`；
    只有着陆页、登录/注册、番剧新闻、站点设置这类公开页在白名单里。

??? question "站点为什么不开放注册？怎么加入？"
    注册需要填**社团群验证码**（环境变量 `GROUP_VERIFICATION_CODE`），而这个码只在社团群里公开。
    填错就拿不到账号——这不是 bug，是入社门槛。

??? question "注册时提示验证码错误 / 收不到邮件验证码？"
    1. 先确认 `GROUP_VERIFICATION_CODE` 填的是**社团群验证码**，不是邮箱验证码（两个字段，别填反）。
    2. **点「发送验证码」直接报「邮件发送失败」= SMTP 没配好**。注册必须经过邮箱验证码，所以 SMTP
       在线上是**必填项**，不是可选项（没配时新社员无法注册，找回密码也不可用）。
    3. 检查 `SMTP_HOST`（QQ 邮箱是 `smtp.qq.com`）、`SMTP_PORT=465`、`SMTP_USE_SSL=true`，
       以及 `MAIL_PASSWORD` 用的是**邮箱授权码**而不是登录密码。
    4. 已有账号登录不受 SMTP 影响。

??? question "登录成功但一刷新就掉线？"
    几乎都是 `SECRET_KEY` 没配、或者每次部署都变。session 用 `SECRET_KEY` 签名，密钥不稳定 → 旧 session 全部失效。
    修法：在 Vercel 环境变量里固定一个长随机字符串，重新部署。

??? question "忘记密码怎么办？"
    登录页点「忘记密码」：输入注册邮箱 → 收重置链接（带签名 token，限时）→ 设置新密码。
    如果邮箱没绑定，需要管理员在后台 `admin/users/bind_email` 帮忙绑定。

??? question "能改用户名 / 头像吗？"
    菜单 →「个人资料」：可以改用户名、上传头像、改密码。
    没上传头像的社员会显示「渐变底色 + 用户名首字」的 SVG 头像（按用户 id 轮换配色），不会是一块空白。

## 部署与数据库

??? question "线上页面 500 了，日志说列/表不存在，怎么办？"
    **部署顺序搞反了**。正确顺序永远是：
    ```bash
    python init.py        # 先在本地对生产库建表/补列
    git push              # 再上代码
    ```
    `init.py` 是幂等的（只建缺失、只加不删），重复跑安全。反过来先上代码 → 新代码查不存在的列 → 必 500。

??? question "报错 NoSuchTableError: users，可表明明存在？"
    表名是**单数**：`user`、`message`、`reply`、`activity`、`photo`；但另外几张又是复数（`contests`、`candidates`…）。
    写裸 SQL 或排查脚本时别当成复数写。（本项目已经被这个问题坑过一次。）

??? question "Supabase 报连接超时 / too many clients？"
    - 优先用 **Transaction pooler**（端口 `6543`，最多 200 并发），别用直连 `5432`（限额 60）。
    - 一次性运维脚本（`init.py`）用直连更稳；日常应用用 pooler。
    - 连接串密码里的特殊字符要 URL 编码。

??? question "Vercel 推送后线上没变化？"
    部署有延迟：通常 1–2 分钟，偶尔 10 分钟以上。**不要**用"页面看起来没变"判断失败，
    去 `/settings` 看页脚**版本号**；还是旧的就再等，或去 Vercel Deployments 看构建状态。

??? question "部署后样式乱 / 还是旧 CSS？"
    静态资源带版本号（`style.css?v=<VERSION>`）。如果版本号没变，说明部署没完成或 `VERSION` 没更新；
    浏览器强刷（Ctrl+F5）再试。

??? question "怎么回滚到上一个版本？"
    Vercel 控制台 → Deployments → 找上一个正常版本 → **Promote to Production**（秒级生效）。
    或者 `git revert <bad-commit>` 再推送。注意：如果坏版本改过表结构，回滚前先确认数据兼容性（本项目"只加不删"，常规回滚安全）。

??? question "域名怎么绑？文档里的示例域名我能用吗？"
    **不能**。文档里的 `your-site.example.com` / `<你的域名>` 都是占位符，不是真实站点。
    你要用自己的域名：自己到域名供应商注册 → 在 Vercel 项目 Settings → Domains 添加你自己的域名 → 在**你自己的域名控制台**加解析记录。
    域名管理权等于站点所有权，不要把任何别人的域名填进你的项目，也不要接受别人让你帮忙绑域名。

??? question "免费额度够用吗？会突然扣费吗？"
    - Supabase / Vercel 免费计划足够小型社团用，赛事期间通常也不会超。
    - AI 好友**只调用免费模型**（`AI_CHAT_MODELS`），全部失败回落 FAQ，不会转付费模型。
    - 萌战 AI 解说走 `OPENAI_API_KEY` / `AI_MODEL` 配置的模型（默认硅基流动，是否计费取决于你配的模型），结果带缓存；量大时先看缓存命中，实在担心就把 `AI_CHAT_ENABLED=false` 关掉聊天、或换免费模型。

## 内容与功能

??? question "新闻详情全是英文？"
    说明机器翻译没生效：检查 `ALIBABA_CLOUD_ACCESS_KEY_ID` / `ALIBABA_CLOUD_ACCESS_KEY_SECRET` 是否配置、额度是否用尽。
    翻译不可用时详情页会显示黄色提示条「当前显示的是原文」，不会让你对着一整页英文猜。
    也可以在「设置 → 语言与翻译」切换译文优先 / 仅原文 / 双语对照。

??? question "AI 好友总是回一条固定的话？"
    说明所有免费模型都不可用（限流/超时/下架），系统回落到了 FAQ 兜底：
    - 看后台 `admin/ai_friend` 的模型调用分布，哪个模型一直失败；
    - 从 `AI_CHAT_MODELS` 里删掉坏模型、换一个免费模型，**不用改代码**；
    - 每天限额（`AI_CHAT_DAILY_LIMIT`，默认 30 条）用完也会走 FAQ。

??? question "AI 好友能换模型吗？要不要花钱？"
    能，改环境变量 `AI_CHAT_MODELS` 即可（逗号分隔，按优先级轮换）。
    请只填**免费模型**：池子里放付费模型才会产生费用，本项目默认配置里没有付费模型。

??? question "萌战阶段/倒计时不对？"
    阶段由 `contest_engine.py` 按赛事的 `open_at/close_at` 和配置天数算出。
    常见原因：**赛事进行中改了阶段天数**（会导致判定错乱）、服务器时间和本地不一致、或赛事状态不是 `open`。
    进行中的赛事不要改赛制参数，要调就等本届结束。

??? question "照片墙图片显示不出来？"
    1. `init.py` 是否建了 `photos` 桶（以及 `avatars`、`contest_images`）；
    2. `SUPABASE_URL` / `SUPABASE_KEY` 是否正确（service_role 才有写权限）；
    3. 桶如果是私有，需要走公开 URL 或签名 URL。

??? question "上传头像/照片失败？"
    - 413：超过 `MAX_CONTENT_LENGTH`（默认 4 MB），前端会先压缩，仍失败就调大上限或换小图；
    - 401/403：`SUPABASE_KEY` 不是 service_role，或桶策略不允许；
    - 前端无反应：看浏览器 Console/Network，多半是 JS 压缩后仍超限。

??? question "通知红点一直不消失 / 收不到邮件通知？"
    红点来自 `/api/notifications/count` 轮询；在「设置 → 通知与免打扰」里可以关掉轮询、按类型静音。
    邮件通知要配 SMTP 且用户没关闭邮件开关。

## 开发与测试

??? question "测试怎么跑？有多少用例？"
    ```bash
    python -m pytest -q          # 单元/集成，690+ 用例（当前 710）
    npx playwright test          # 端到端（会自己起服务，需 5000 端口空闲）
    ```

??? question "为什么 CI 上测试挂了，本地却是绿的？"
    十有八九是测试**依赖了本机 `.env` 或外部 API**。CI 没有 `.env`、也不能保证外网。
    规范：需要环境变量的用例用 `monkeypatch.setenv` 自带假值，外部调用一律 mock。
    （真实事故：AI 好友 4 个用例依赖本机 `OPENAI_API_KEY`，CI 上必挂。）

??? question "文档和 README 里的截图是哪来的？是真实社员吗？"
    不是真实数据。所有截图来自本地 `TESTING=1` 演示环境（演示账号 `testuser`、演示帖子、演示活动、演示照片），
    这样既能讲清界面，又不会泄露社员信息。

??? question "本地启动报 ModuleNotFoundError: werkzeug（但 pip 装过了）？"
    Windows 上可能有两个 Python 3.14：`py` 和 `C:\Python314\python.exe`。
    Playwright 的 webServer 用后者启动，依赖要两套都装：
    ```powershell
    py -m pip install -r requirements.txt
    C:\Python314\python.exe -m pip install -r requirements.txt
    ```

??? question "改 VERSION 文件后版本号显示奇怪 / 带不可见字符？"
    文件被写入了 UTF-8 BOM。`VERSION` 必须是**无 BOM、LF** 的纯文本（`write` 工具或编辑器保存），
    别用会写 BOM 的命令（如旧版 PowerShell 的 `Set-Content -Encoding UTF8`）。

??? question "改中文文件后出现乱码？"
    不要用 PowerShell 做"读 UTF-8 → 改 → 写回"（Windows PowerShell 5.1 会按 ANSI 解码）。
    用编辑器 / `edit` 工具，或 Python 脚本显式 `encoding='utf-8'`。

## 维护与发布

??? question "发版流程是什么？"
    ```bash
    # 1) 改 VERSION（无 BOM）
    # 2) 提交并推送 main
    git add -A && git commit -m "feat: xxx（v4.25.0）" && git push
    # 3) 打 tag 并推送
    git tag -a v4.25.0 -m "v4.25.0：xxx" && git push origin v4.25.0
    ```
    tag 会触发 `version.yml` 同步 `VERSION`；`release.yml` **只对 `vX.Y.0` 自动建 GitHub Release**，
    补丁版本只打 tag（避免发行版太频繁）。

??? question "GitHub 上只有 tag 没有 Release，是不是漏了？"
    是故意的：只有功能版本（`x.y.0`）会自动生成 Release，补丁版本不发。
    需要补发某个版本时，去 Actions → Release → Run workflow 手动指定 tag。

??? question "数据怎么备份？"
    Supabase 有每日自动备份；重大改动前手动 `pg_dump "$DATABASE_URL" --data-only --no-owner > backup.sql`。
    Storage 里的图片（头像/照片/赛事图）不在数据库备份里，需要单独导出。

??? question "怎么改社团名 / 站点标题？"
    改环境变量 `CLUB_NAME`，重新部署；页脚、标题、AI 人设提示词都会跟着变。

??? question "怎么改主题色 / 字体？"
    主题色在 `static/css/style.css` 顶部的 CSS 变量（`--accent` 等），浅色/深色各一套。
    字体调 `--font-*` 相关变量；改完记得升版本号让 `style.css?v=` 刷新。

??? question "怎么彻底关掉 AI 功能（包括花钱的解说）？"
    - AI 好友：`AI_CHAT_ENABLED=false`（前端会提示未配置，聊天直接走 FAQ）。
    - 萌战解说/预测：把 `OPENAI_API_KEY` 清空或删掉，解说功能会降级（页面仍可看数据）。
    - 新闻翻译：删掉 `ALIBABA_CLOUD_ACCESS_KEY_*`，详情页会显示"当前显示的是原文"提示。

??? question "服务端报错去哪看？"
    Vercel 项目 → Logs（函数日志），按时间/状态码筛。数据库慢查询去 Supabase Dashboard 的 Query Performance。

??? question "断网了为什么还能看到「网络开小差」页面？"
    这是 PWA 的离线兜底页（`/offline`）。Service Worker 预缓存了离线页和核心静态资源，
    页面导航请求断网时会自动回落到它；已缓存的静态资源（样式、图标）也还能用。
    想体验：先把站点添加到主屏幕或刷新一次让 SW 接管，然后断网打开。

??? question "追番清单的数据存在哪？会把我的看番记录公开吗？"
    存在数据库 `anime_list` 表，只和你的账号关联；目前**只有你自己**能在「我的追番」看到，
    不会出现在社员名单或他人页面。移除列表里的番只删你自己的记录，不影响番剧百科本身。

??? question "不想收每周邮件周报怎么办？"
    周报每封信底部都有「点此一键退订」链接，点一下立即关闭邮件通知（无需登录，链接 30 天有效）。
    也可以登录后去「设置 → 通知」把「邮件通知」关掉；站内消息不受影响。

??? question "微信/QQ 分享链接不显示封面图？"
    - **微信**：不执行页面 JS，也不读 `og:` 标签；没有接微信 JS-SDK 时，它只抓页面里第一张真实 `<img>`
      （太小的图会被忽略）。所以首页（canvas 动画封面）特意放了一张 `1200×630` 的静态缩略图，
      **视觉上整个藏到屏幕外**，只给爬虫抓，不影响封面排版。
    - **QQ / QQ 空间**：读 `og:image`（已配置 1200×630 绝对地址，且不带 `?v=` 查询参数，避免抓取器因 query 判重）。
    - 站内页面需要登录，微信/QQ 爬虫会被 302 到登录页；**分享站内内容请用公开分享页**
      `/share` 或 `/share/contest/<赛事ID>`（赛事详情页有「🔗 复制分享链接」按钮）。
    - 微信会强缓存旧的分享卡片，换图后可以把链接加个 `?r=随机数` 再发一次，绕开旧缓存。
    - **历史坑**：`robots.txt` 早期是 `Disallow: /` 全站禁抓，连 `/static/images/og-cover.png`
      都拦了，微信/QQ 爬虫拿不到图 → 任何分享都无缩略图。v4.31.2 已改为只拦后台/私有路径，
      并显式放行 MicroMessenger / QQ / Twitterbot 等分享爬虫；v4.32.0 起 `robots.txt` 与
      `sitemap.xml` 都由后端按**当前请求域名**动态生成，换域名不用改代码、也不会写死线上域名。
