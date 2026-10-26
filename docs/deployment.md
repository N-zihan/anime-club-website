# 平台部署（Vercel）

!!! info "本文档不含 Vercel / Supabase 控制台截图"
    控制台页面会暴露环境变量、连接串、API Key 等敏感信息。所以这里用仓库里的配置文件、
    命令输出和公开页面来说明流程——照着做即可，不需要任何后台截图对照。

!!! warning "域名一律用占位符"
    文档里所有域名都是占位符（`your-site.example.com` / `<你的域名>`）。
    这份文档面向任何想自建社团站的人：**域名请自己注册、自己绑定**，不要把任何别人的域名
    填进自己的项目，也不要照抄成解析记录。域名管理权等于站点所有权。

## 1. 部署拓扑

```text
GitHub（main 分支 push）
   │
   ▼
Vercel 自动构建 ──► Python Serverless 函数（@vercel/python，hkg1 香港区）
   │                     │
   │                     ├──► Supabase PostgreSQL（数据）
   │                     └──► Supabase Storage（头像 / 照片 / 赛事图）
   ▼
https://<你的域名>        # 每个部署用自己的域名
```

仓库根目录的 `vercel.json` 决定构建方式（下面是当前仓库的真实内容）：

```json
{
  "version": 2,
  "regions": ["hkg1"],
  "builds": [
    {
      "src": "run.py",
      "use": "@vercel/python",
      "config": {
        "pythonVersion": "3.14",
        "maxDuration": 60,
        "installCommand": "pip install -r requirements.txt"
      }
    }
  ],
  "routes": [{ "src": "/(.*)", "dest": "run.py" }],
  "crons": [
    { "path": "/api/cron/weekly-digest", "schedule": "0 1 * * 1" },
    { "path": "/api/cron/weekly-digest-resume", "schedule": "30 1 * * 1" }
  ]
}
```

要点：

- **`run.py` 是唯一入口**：`routes` 把 `/(.*)` 全部转发给它，Vercel 用 `@vercel/python` 直接跑 Flask，
  不需要 gunicorn；本地开发才用 `python run.py`。
- **所有请求都经过这个函数**，包括 `/static/...`：静态文件由 Flask 提供（实测响应头带
  `Vary: Cookie`、`X-Vercel-Id`），Vercel 只在边缘做传输/缓存。因此 CSS/JS 更新靠
  `?v=<VERSION>` 版本参数刷新，而不是靠重新上传静态文件。
- `maxDuration: 60` 是函数执行上限（AI 调用、新闻抓取都靠它兜住；超时会 504）。
- `regions: hkg1` 选香港区，国内访问比美区快。
- `crons`：每周一 01:00 UTC（北京时间 09:00）调用周报接口，见下文 `CRON_SECRET`。
- **`robots.txt` 与 `sitemap.xml` 由后端动态生成**（`app/public.py`）：所有 URL 按当前请求域名拼接，
  仓库里没有这两个静态文件。换域名、多域名部署、Vercel 预览域名都会自动适配，不需要改代码或重新上传。

## 2. 环境变量清单

在 Vercel 项目 **Settings → Environment Variables** 里配置（Production / Preview 都建议配齐；
键名以仓库根目录 `.env.example` 为准）：

| 变量 | 必填 | 说明 |
|---|---|---|
| `DATABASE_URL` | ✅ | Supabase Postgres 连接串，建议用 `6543` 端口的 pooler |
| `SECRET_KEY` | ✅ | Flask session 签名密钥，随机长字符串；**必须固定**，否则每次部署都会掉登录 |
| `GROUP_VERIFICATION_CODE` | ✅ | 注册时的社团群验证码，只有社员知道 |
| `CLUB_NAME` | ✅ | 站点名（标题 / 页脚 / AI 人设都会用到） |
| `SUPABASE_URL` / `SUPABASE_KEY` / `SUPABASE_ANON_KEY` | ✅ | 头像、照片、赛事图上传用；`SUPABASE_KEY` 是 service_role，仅后端使用 |
| `SMTP_HOST` / `SMTP_PORT` / `SMTP_USE_SSL` / `MAIL_USERNAME` / `MAIL_PASSWORD` | ✅ **注册必需** | 注册邮箱验证码、找回密码、邮件通知。不配则验证码发不出去，**新社员无法注册** |
| `CRON_SECRET` | 用周报时必填 | Vercel Cron 调用 `/api/cron/weekly-digest` 的鉴权密钥；未配置时接口返回 503 |
| `SITE_URL` | 选填 | 通知里绝对链接的基地址；不配则退化为当前请求的域名 |
| `OPENAI_API_KEY` / `OPENAI_BASE_URL` / `AI_MODEL` | 选填 | 萌战 AI 解说 / 预测（OpenAI 兼容接口，默认硅基流动） |
| `AI_CACHE_ENABLED` / `AI_CACHE_TTL` / `AI_CACHE_MAX_ENTRIES` / `AI_TIMEOUT` | 选填 | AI 结果缓存与超时 |
| `AI_CHAT_ENABLED` / `AI_CHAT_MODELS` / `AI_CHAT_TIMEOUT` / `AI_CHAT_MAX_TRIES` / `AI_CHAT_COOLDOWN_429` / `AI_CHAT_COOLDOWN_ERROR` / `AI_CHAT_DAILY_LIMIT` | 选填 | AI 虚拟好友；模型池只放免费模型，全失败时回落站内 FAQ |
| `ALIBABA_CLOUD_ACCESS_KEY_ID` / `ALIBABA_CLOUD_ACCESS_KEY_SECRET` | 选填 | 新闻正文机器翻译；不配则显示原文 |
| `ANI_NEWS_API` | 选填 | AniNewsAPI 上游地址（如 `https://news.your-site.example.com`）；不配则自动按当前请求域名推导 `news.<域名>`，没有该服务时新闻页优雅降级 |
| `MAX_CONTENT_LENGTH` | 选填 | 上传体积上限（字节），默认 `4194304`（4 MB） |
| `SESSION_COOKIE_SECURE` | 选填 | 会话 Cookie 的 Secure 标志；跟随部署平台（Vercel 为开，本地为关），可显式覆盖 |
| `SESSION_TIMEOUT_MINUTES` | 选填 | 登录会话有效分钟数 |
| `AVATAR_MAX_SIZE` | 选填 | 头像文件大小上限（字节），默认 2 MB |
| `VOTE_POLL_SECONDS` | 选填 | 投票页实时票数轮询间隔（秒），默认 10，范围 3~60 |
| `APP_VERSION` | 选填 | 版本号覆盖；正常由 `VERSION` 文件 / git tag 自动解析 |
| `GITHUB_REPO` / `GITHUB_TOKEN` | 选填 | 站内「更新记录」从 GitHub 拉取 commit |

!!! danger "生产环境不要设置这三个变量"
    - `TESTING`：只给本地演示 / 自动化测试用。`run.py` 里的演示数据种子只在
      「TESTING=1 + 非 Vercel + SQLite 库」同时满足时才执行（v4.31.8 起有硬校验），
      生产库会拒绝写入，但**不要配**，避免踩到历史坑（详见 [开发约定与踩坑](dev-notes.md) 3.6）。
    - `INIT_DB`：会让函数冷启动时执行 `db.create_all()`，拖慢启动并占用数据库连接。
      表结构同步请用一次性的 `python init.py`。
    - `DISABLE_CSRF_FOR_TESTS`：会关掉 CSRF 保护，生产环境绝对不许开。

!!! warning "SMTP 不是可选项：不配就没法注册新社员"
    注册流程是「填邮箱 → 收 6 位验证码 → 提交注册」，验证码只能通过 SMTP 寄出。
    没配 `MAIL_USERNAME` / `MAIL_PASSWORD` 时，`/send_register_code` 会直接返回
    「邮件发送失败」，新社员卡在第一步；**找回密码**同样不可用。
    已有账号登录、站内功能不受影响，所以本地演示模式可以不配。

!!! danger "密钥只进 Vercel 控制台，不进仓库"
    `.env` 已在 `.gitignore` 里。任何人往仓库里提交真实密钥，等于把数据库和邮件账号送人。

## 3. 首次部署步骤

1. 创建你自己的 GitHub 仓库，把代码推到 `main` 分支。
2. 访问 [Vercel](https://vercel.com)，用 GitHub 登录 → **Add New → Project** → 选择你的仓库导入。
   仓库里有 `vercel.json`，Vercel 会按它自动构建 Flask 应用；如果没识别出来，Framework 手动选 **Other**。
3. 在 **Environment Variables** 里添加上面清单里的变量（键名对齐 `.env.example`）。
4. **先在本地对生产库执行一次表结构初始化**：`python init.py`（顺序见 [数据库](database.md)），再点 Deploy。
5. Deploy 后等待 1–2 分钟，Vercel 会分配一个 `xxx.vercel.app` 域名，先用它访问 `/settings`
   看页脚版本号，确认部署成功。
6. 绑定你自己的域名：在 Vercel 项目 **Settings → Domains** 添加**你自己注册的域名**，
   按提示在**你自己的域名服务商控制台**添加 CNAME / A 记录。

!!! note "关于 ICP 备案"
    如果要走 ICP 备案，网站必须接入**境内**服务器/接入商，纯 Vercel（境外节点）不满足备案条件；
    需要备案就换境内托管（届时同一条命令 `python run.py` / gunicorn 一样能跑，见 [运维手册](operations.md)）。
    不打算备案也可以正常使用 Vercel，只是访问稳定性视网络环境而定。

!!! note "国内访问"
    Vercel 默认域名的访问稳定性视网络环境而定，自定义域名 + 香港区会好一些。
    如果暂时不打算上公网，也可以只本地运行：`python run.py` 后访问 <http://127.0.0.1:5000>。

## 4. 部署后验证（每次都做）

把下面的 `SITE` 换成**你自己的**域名（或 Vercel 分配的 `xxx.vercel.app`）：

```bash
SITE="https://your-site.example.com"

# 1) 版本号是否是新版本（/settings 页脚会渲染当前 VERSION）
curl -s "$SITE/settings" | grep -o 'v[0-9]\+\.[0-9]\+\.[0-9]\+' | head -1

# 2) 静态资源版本（防止旧 CSS 缓存）
curl -s "$SITE/settings" | grep -o 'style.css?v=[^"]*' | head -1

# 3) 公开页是否 200（落地页 / 登录 / 新闻 / 设置）
for p in / /login /anime_news /settings; do
  echo -n "$p -> "; curl -s -o /dev/null -w '%{http_code}\n' "$SITE$p"
done

# 4) 访客拦截是否生效（内部页面应该 302 到登录）
curl -s -o /dev/null -w '%{http_code} %{redirect_url}\n' "$SITE/board"

# 5) 分享缩略图能否被抓取（微信/QQ 靠它出图）
curl -s -o /dev/null -w '%{http_code} %{content_type}\n' "$SITE/static/images/og-cover.png"
curl -s "$SITE/" | grep -o 'property="og:image" content="[^"]*"' | head -1

# 6) robots / sitemap 是否按当前域名动态生成（应输出你自己的域名）
curl -s "$SITE/robots.txt" | grep -i '^Sitemap:'
curl -s "$SITE/sitemap.xml" | grep -o '<loc>[^<]*</loc>' | head -3
```

!!! warning "Vercel 部署有延迟"
    推送后线上不会立刻更新，通常 1–2 分钟，偶尔更久。别用"页面看起来没变"判断失败——
    用上面的**版本号**确认。

## 5. 回滚

两种方式：

1. **Vercel 控制台**：Deployments → 找到上一个正常版本 → **Promote to Production**（秒级生效，推荐）。
2. **Git 回滚**：`git revert <bad-commit>` 后推送，走一次正常部署。

!!! danger "回滚注意数据库"
    如果坏版本**已经改了表结构**（新列/新表），回滚代码后旧代码通常还能跑（多出来的列不影响）；
    但如果是**删列/改类型**，就必须先恢复数据。本项目约定"只加不删"（`init.py` 只加列），
    所以常规回滚是安全的。

## 6. GitHub Actions 一览

| 文件 | 触发 | 作用 |
|---|---|---|
| `python-app.yml` | push / PR（main） | 装依赖 → flake8（`E9,F63,F7,F82` 会阻塞；其余规则 `--exit-zero` 不阻塞）→ `pytest --cov --cov-fail-under=70`（覆盖率低于 70% 会失败） |
| `node.js.yml`（Playwright Tests） | push / PR（main） | 装 Python 依赖 + `npm ci` + Playwright 浏览器 → `npx playwright test` |
| `pylint.yml` | push | `pylint . --fail-under=8.5 --ignore=tests`，**低于 8.5 会失败** |
| `codeql.yml` | push / PR（main）+ 每周定时 | CodeQL 安全扫描 |
| `patrol.yml` | 每周一定时 / 手动 | 线上巡检：`/api/health`（版本 + 数据库）+ 公开页 / robots / sitemap / 分享图状态码；域名由仓库变量 `SITE_URL` 提供，未配置则跳过 |
| `version.yml` | 推 tag | 把 `VERSION` 文件同步成 tag 版本号 |
| `release.yml` | 推 `v*` tag / 手动 | 自动只为 `vX.Y.0` 建 GitHub Release，补丁版本跳过；手动可补建任意 tag |
| `docs.yml` | push main 且改动 `docs/**` 或 `mkdocs.yml` | 构建文档站并部署到 GitHub Pages |

## 7. 常见部署故障

| 现象 | 排查 |
|---|---|
| 函数 500，日志 `ModuleNotFoundError` | 依赖没写进 `requirements.txt`，或本地与 Vercel 的 Python 版本不一致 |
| 冷启动很慢 / 偶尔 504 | 函数里做了重活（全量查询、图片处理）；把重活挪到后台或加缓存，注意 `maxDuration: 60` |
| 页面样式乱 / 还是旧样式 | `style.css?v=` 没变：确认 `VERSION` 已更新并部署成功；浏览器强刷 |
| 登录后立刻掉线 | `SECRET_KEY` 没配或每次部署变化；固定它 |
| 注册点「发送验证码」报邮件发送失败 | SMTP 未配或配错（`SMTP_HOST` / `SMTP_PORT` / `SMTP_USE_SSL` / `MAIL_USERNAME` / `MAIL_PASSWORD`）；QQ 邮箱的 `MAIL_PASSWORD` 要用授权码 |
| 上传图片 413 | 超过 `MAX_CONTENT_LENGTH`（默认 4 MB）；前端压缩或调大上限 |
| 通知里的链接是 `localhost` | 配 `SITE_URL` |
| 首页正常、AI 500 | `OPENAI_API_KEY` 缺失或额度用尽；AI 好友会回落 FAQ，解说会降级 |
| 周报 cron 返回 403 / 503 | 403：`CRON_SECRET` 不匹配；503：没配 `CRON_SECRET`（接口拒绝裸奔） |

![着陆页](screenshots/splash.webp)
