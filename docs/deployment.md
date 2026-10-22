# 平台部署（Vercel）

!!! info "本文档不含 Vercel / Supabase 控制台截图"
    控制台页面会暴露环境变量、连接串、API Key 等敏感信息。所以这里用配置文件、命令输出和公开页面截图来说明流程——照着做即可，不需要任何后台截图对照。

## 1. 部署拓扑

```text
GitHub (main 分支 push)
   │
   ▼
Vercel 自动构建 ──► Python Serverless 函数（@vercel/python, hkg1 香港区）
   │                     │
   │                     ├──► Supabase PostgreSQL（数据）
   │                     └──► Supabase Storage（头像 / 照片 / 赛事图）
   ▼
https://www.nanyi-anime-club.top
```

- 仓库根目录的 `vercel.json` 决定构建方式：

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
  "routes": [{ "src": "/(.*)", "dest": "run.py" }]
}
```

要点：

- **`run.py` 就是 WSGI 入口**，Vercel 直接跑它，不需要 gunicorn。
- `maxDuration: 60` 是函数上限（AI 调用、新闻抓取都靠它兜住）。
- `regions: hkg1` 选香港，国内访问比美区快。
- 静态文件由 Vercel 边缘直接返回，不经过 Python 函数。

## 2. 环境变量清单

在 Vercel 项目 **Settings → Environment Variables** 里配置（Production / Preview 都建议配齐）：

| 变量 | 必填 | 说明 |
|---|---|---|
| `DATABASE_URL` | ✅ | Supabase 连接串（建议 `6543` pooler） |
| `SECRET_KEY` | ✅ | Flask session 签名密钥，随机长字符串 |
| `GROUP_VERIFICATION_CODE` | ✅ | 注册时的社团群验证码，只有社员知道 |
| `CLUB_NAME` | ✅ | 站点名（页脚 / 标题 / AI 人设都会用到） |
| `SUPABASE_URL` / `SUPABASE_KEY` / `SUPABASE_ANON_KEY` | ✅ | 头像、照片、赛事图上传用（`SUPABASE_KEY` 是 service_role，仅后端；`ANON_KEY` 给前端） |
| `SMTP_HOST` / `SMTP_PORT` / `SMTP_USE_SSL` / `MAIL_USERNAME` / `MAIL_PASSWORD` | 选填 | 验证码、找回密码邮件；不配则只写日志 |
| `SITE_URL` | 选填 | 通知里的绝对链接；不配则用 `request.url_root` |
| `OPENAI_API_KEY` / `OPENAI_BASE_URL` / `AI_MODEL` | 选填 | 萌战 AI 解说/预测（OpenAI 兼容接口，默认硅基流动） |
| `AI_CHAT_ENABLED` / `AI_CHAT_MODELS` / `AI_CHAT_DAILY_LIMIT` | 选填 | AI 虚拟好友；**模型池只放免费模型**，失败回落 FAQ |
| `ALIBABA_CLOUD_ACCESS_KEY_ID` / `ALIBABA_CLOUD_ACCESS_KEY_SECRET` | 选填 | 新闻正文机器翻译；不配则显示原文并提示 |
| `MAX_CONTENT_LENGTH` | 选填 | 上传体积上限（字节），默认 4 MB |
| `SESSION_COOKIE_SECURE` | 选填 | Cookie Secure 标志；Vercel 默认开，本地默认关 |
| `APP_VERSION` | 选填 | 版本号覆盖，正常由 `VERSION` 文件/tag 自动解析 |
| `GITHUB_REPO` / `GITHUB_TOKEN` | 选填 | 站点内嵌的更新记录（changelog）功能 |

!!! danger "密钥只进 Vercel 控制台，不进仓库"
    `.env` 已在 `.gitignore` 里。任何人往仓库里提交真实密钥，等于把数据库和邮件账号送人。

## 3. 首次部署步骤

1. 创建你自己的 GitHub 仓库，把代码推到 `main` 分支。
2. 访问 [Vercel](https://vercel.com)，用 GitHub 登录 → **Add New → Project** → 选择你的仓库导入。
   - 仓库里有 `vercel.json`，Vercel 会按它自动构建 Flask 应用；如果没识别出来，Framework 手动选 **Other** 即可。
3. 在 **Environment Variables** 里添加环境变量（参考上面的清单，或照着你自己 `.env` 的键值填）。
4. **先在本地对生产库执行一次迁移**：`python init.py`（顺序见 [数据库](database.md)），再点 Deploy。
5. Deploy 后等待 1–2 分钟，Vercel 会分配一个 `xxx.vercel.app` 域名，先用它访问 `/settings` 看页脚版本号。
6. **域名请自己去域名供应商注册**（如 [NameSilo](https://www.namesilo.com)，海外商注册无需备案；若需备案就不能用 Vercel，要换国内服务器）。
   买好域名后，在 Vercel 项目 **Settings → Domains** 添加 **你自己的域名**，按提示在域名服务商加 CNAME / A 记录。

!!! danger "别把别人的域名绑到你的项目"
    文档里出现的 `www.nanyi-anime-club.top` 是**本社团自己的线上域名**，仅用于演示"怎么验证部署"。
    - 你部署自己的站点时，**不要**把它填进 Domains，也不要照抄成 CNAME 目标；
    - 你自己的域名要做解析，只能在**你注册的那个域名**的控制台里操作；
    - 反过来也一样：不要接受任何"帮别人绑定域名"的操作，域名管理权等于站点所有权。

!!! note "国内访问"
    Vercel 的默认域名在国内可能不稳定，自定义域名 + 香港区域会好一些；极端情况可在前面套一层 CDN/反向代理。
    如果暂时不打算上公网，也可以只本地运行：`python run.py` 后访问 <http://127.0.0.1:5000>。

## 4. 部署后验证（每次都做）

把下面的 `https://your-site.example.com` 换成**你自己的**域名（或 Vercel 分配的 `xxx.vercel.app`），
不要用文档示例里的社团域名去验证你的部署。

```bash
SITE="https://your-site.example.com"

# 1) 版本号是否是新版本（页脚 / 设置页会渲染当前 VERSION）
curl -s "$SITE/settings" | grep -o 'v4\.[0-9.]*' | head -1

# 2) 静态资源版本（防止旧 CSS 缓存）
curl -s "$SITE/settings" | grep -o 'style.css?v=[^"]*' | head -1

# 3) 公开页是否 200（新闻/着陆/登录）
for p in / /login /anime_news /settings; do
  echo -n "$p -> "; curl -s -o /dev/null -w '%{http_code}\n' "$SITE$p"
done

# 4) 访客拦截是否生效（内部页面应该 302 到登录）
curl -s -o /dev/null -w '%{http_code} %{redirect_url}\n' "$SITE/board"
```

!!! warning "Vercel 部署有延迟"
    推送后线上不会立刻更新，通常 1～2 分钟，偶尔 10 分钟以上。别用"页面没变"判断部署失败——用上面的**版本号**确认。

## 5. 回滚

两种方式：

1. **Vercel 控制台**：Deployments → 找到上一个正常版本 → **Promote to Production**（秒级生效，推荐）。
2. **Git 回滚**：`git revert <bad-commit>` 后推送，走一次正常部署。

!!! danger "回滚注意数据库"
    如果坏版本**已经改了表结构**（新列/新表），回滚代码后旧代码通常还能跑（多出来的列不影响）；但如果是**删列/改类型**，就必须先恢复数据备份。本项目约定"只加不删"，所以常规回滚是安全的。

## 6. GitHub Actions 一览

| 工作流 | 触发 | 作用 |
|---|---|---|
| `python-app.yml` | push / PR | 装依赖 → flake8（只查语法级错误）→ `pytest --cov` |
| `node.js.yml` | push / PR | `npm ci` → 装 Playwright → 跑 e2e |
| `pylint.yml` | push / PR | 静态检查（不阻塞合入） |
| `codeql.yml` | push / 定时 | 安全扫描 |
| `version.yml` | 推 tag | 把 `VERSION` 文件同步成 tag 版本号 |
| `release.yml` | 推 `vX.Y.0` 标签 / 手动 | 自动发布 GitHub Release（**补丁版本不发**，避免发行版太频繁） |
| `docs.yml` | 改 `docs/**` / `mkdocs.yml` | 构建文档站并部署到 GitHub Pages |

## 7. 常见部署故障

| 现象 | 排查 |
|---|---|
| 函数 500，日志 `ModuleNotFoundError` | 依赖没写进 `requirements.txt`，或版本与本地不一致 |
| 冷启动很慢 / 偶尔 504 | 函数里做了重活（全量查询、图片处理）；把重活挪到后台或加缓存 |
| 页面样式乱 / 还是旧样式 | `style.css?v=` 没变：确认 `VERSION` 已更新并部署成功；浏览器强刷 |
| 登录后立刻掉线 | `SECRET_KEY` 没配/每次部署变化；固定它 |
| 上传图片 413 | 超过 `MAX_CONTENT_LENGTH`；前端压缩或调大上限 |
| 通知里的链接是 `localhost` | 配 `SITE_URL` |
| 首页正常、AI 500 | `OPENAI_API_KEY` 缺失或额度用尽；AI 好友会自动回落 FAQ，解说会降级 |

![着陆页](screenshots/splash.webp)
