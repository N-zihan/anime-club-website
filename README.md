<div align="center">

# 动漫社网站

[![Vercel](https://img.shields.io/badge/Deploy-Vercel-000000?logo=vercel)](https://vercel.com)
[![Python](https://img.shields.io/badge/Python-3.14-3776AB?logo=python)](https://python.org)
[![Flask](https://img.shields.io/badge/Flask-3.1-000000?logo=flask)](https://flask.palletsprojects.com)
[![Supabase](https://img.shields.io/badge/Supabase-PostgreSQL-3ECF8E?logo=supabase)](https://supabase.com)
[![Python application](https://github.com/N-zihan/anime-club-website/actions/workflows/python-app.yml/badge.svg)](https://github.com/N-zihan/anime-club-website/actions/workflows/python-app.yml)
[![Playwright Tests](https://github.com/N-zihan/anime-club-website/actions/workflows/node.js.yml/badge.svg)](https://github.com/N-zihan/anime-club-website/actions/workflows/node.js.yml)
[![Pylint](https://github.com/N-zihan/anime-club-website/actions/workflows/pylint.yml/badge.svg)](https://github.com/N-zihan/anime-club-website/actions/workflows/pylint.yml)
[![CodeQL Advanced](https://github.com/N-zihan/anime-club-website/actions/workflows/codeql.yml/badge.svg)](https://github.com/N-zihan/anime-club-website/actions/workflows/codeql.yml)
[![Docs](https://github.com/N-zihan/anime-club-website/actions/workflows/docs.yml/badge.svg)](https://n-zihan.github.io/anime-club-website/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Version](https://img.shields.io/github/v/tag/N-zihan/anime-club-website?label=Version)](https://github.com/N-zihan/anime-club-website/tags)

> 建站即用 · 开箱即改

动漫社网站：社团展示、社员论坛、番剧资源与百科、动漫资讯、线上萌战、AI 看板娘

> 本站为社团内部使用：**仅社员登录访问、不对外开放注册**，因此不提供公开的线上入口。
> 部署 / 改造请照文档操作，并**使用你自己的域名**。

📚 **文档**：[n-zihan.github.io/anime-club-website](https://n-zihan.github.io/anime-club-website/)
（本地开发、Supabase 建库、Vercel 部署、站内模块细节、常见问题、运维与踩坑）

</div>

## 功能概览

| 功能 | 说明 |
|---|---|
| 用户系统 | 注册（社团群验证码）、登录、邮箱绑定、密码重置、头像上传 |
| 社员论坛 | 主题帖 + 楼层回复，按最后活跃排序、分页（楼层也分页），帖子页底部固定输入框；作者可编辑/删除自己的帖子、回复，可举报他人内容 |
| 番剧资源 | 社员推荐 → 管理员审核 → 公开展示；视频源白名单解析 |
| 番剧百科 | Bangumi API 条目，分页浏览与搜索；「想看 / 在看 / 看过」一键加入**追番清单** |
| 动漫新闻 | 聚合资讯、全文阅读、机器翻译（译文优先 / 仅原文 / 双语对照）、视频嵌入提取、RSS 订阅 |
| 站内搜索 | 番剧 / 新闻 / 资源关键词搜索，搜索历史本地保存 |
| 照片墙 | 活动照片分类展示，存储于 Supabase Storage；照片可举报 |
| 萌战系统 | 提名 → 审核 → 海选 → 小组赛 → 淘汰赛 → 决赛，内置禁赛 / 轮空 / 复活赛守则；**实时票数 + 对抗进度条**、对阵图、一键生成分享海报 |
| AI 虚拟好友 | 免费模型池聊天 + 自定义角色人格（仅自己 / 公开），限额 + FAQ 兜底，不产生付费调用 |
| 通知中心 | 回复 / 审核 / 赛事 / 系统通知，分页、清空已读、按类型静音、关闭红点轮询、邮件提醒开关 |
| 分享卡片 | 公开分享页 `/share`、`/share/contest/<id>`，微信/QQ 分享可显示封面缩略图 |
| 邮件周报 | 每周一自动汇总番剧新闻（摘要+配图）+ 赛事阶段/截止时间发到社员邮箱，带一键退订 |
| 站点设置 | 主题、字号、动效、新闻显示语言、通知免打扰、本机数据清理 |
| PWA / 离线 | 可添加到主屏幕；断网时回落到品牌离线页，静态资源走 Service Worker 缓存 |
| 管理后台 | 用户 / 帖子 / 活动 / 照片 / 赛事 / 通知 / AI 人设审核、成员数据导出、举报审核队列、**操作日志留痕** |

## 页面导览

> ⚠️ **截图全部为样例数据**：演示账号、示例活动、示例帖子与照片，由本地测试环境生成，不含真实社员信息；新闻页取自公开媒体。

<details open>
<summary><b>入口与公开页</b></summary>

<table>
  <tr>
    <td align="center" width="50%"><img src="docs/screenshots/splash.webp" alt="着陆页" width="440"><br><sub><b>着陆页</b> · 访客首屏，引导登录 / 注册</sub></td>
    <td align="center" width="50%"><img src="docs/screenshots/login.webp" alt="登录页" width="440"><br><sub><b>登录页</b> · 用户名 / QQ 号 / 邮箱登录</sub></td>
  </tr>
  <tr>
    <td align="center"><img src="docs/screenshots/news.webp" alt="动漫新闻" width="440"><br><sub><b>动漫新闻</b> · 聚合资讯、来源筛选、显示语言切换</sub></td>
    <td align="center"><img src="docs/screenshots/news-detail.webp" alt="新闻详情" width="440"><br><sub><b>新闻详情</b> · 全文 + 译文 / 原文 / 双语对照</sub></td>
  </tr>
</table>
</details>

<details open>
<summary><b>社员页面</b></summary>

<table>
  <tr>
    <td align="center" width="50%"><img src="docs/screenshots/home.webp" alt="首页" width="440"><br><sub><b>首页</b> · 站点入口卡片与引导</sub></td>
    <td align="center" width="50%"><img src="docs/screenshots/forum.webp" alt="社员论坛" width="440"><br><sub><b>社员论坛</b> · 主题帖列表、分页</sub></td>
  </tr>
  <tr>
    <td align="center"><img src="docs/screenshots/thread.webp" alt="帖子详情" width="440"><br><sub><b>帖子详情</b> · 楼层回复、底部固定输入框</sub></td>
    <td align="center"><img src="docs/screenshots/profile.webp" alt="个人主页" width="440"><br><sub><b>个人主页</b> · 头像、身份、加入时间</sub></td>
  </tr>
  <tr>
    <td align="center"><img src="docs/screenshots/members.webp" alt="社员名单" width="440"><br><sub><b>社员名单</b> · 头像 + 身份标识</sub></td>
    <td align="center"><img src="docs/screenshots/settings.webp" alt="设置页" width="440"><br><sub><b>设置页</b> · 外观 / 翻译 / 通知 / 隐私</sub></td>
  </tr>
  <tr>
    <td align="center"><img src="docs/screenshots/contest-center.webp" alt="赛事中心" width="440"><br><sub><b>赛事中心</b> · 进行中 / 即将开始 / 已结束</sub></td>
    <td align="center"><img src="docs/screenshots/contest-detail.webp" alt="赛事详情" width="440"><br><sub><b>赛事详情</b> · 阶段进度、提名 / 投票、AI 赛事中心</sub></td>
  </tr>
  <tr>
    <td align="center"><img src="docs/screenshots/ai-friend.webp" alt="AI 好友" width="440"><br><sub><b>AI 虚拟好友</b> · 免费模型聊天 + 自定义人格</sub></td>
    <td align="center"><img src="docs/screenshots/gallery.webp" alt="照片墙" width="440"><br><sub><b>照片墙</b> · 活动照片分类展示</sub></td>
  </tr>
</table>
</details>

<details>
<summary><b>管理后台</b></summary>

<table>
  <tr>
    <td align="center" width="50%"><img src="docs/screenshots/admin-dashboard.webp" alt="站长面板" width="440"><br><sub><b>站长面板</b> · 各管理模块入口</sub></td>
    <td align="center" width="50%"><img src="docs/screenshots/admin-messages.webp" alt="帖子管理" width="440"><br><sub><b>帖子管理</b> · 删除帖子 / 回复</sub></td>
  </tr>
  <tr>
    <td align="center"><img src="docs/screenshots/admin-users.webp" alt="用户管理" width="440"><br><sub><b>用户管理</b> · 权限、邮箱、重置密码、导出</sub></td>
    <td align="center"><img src="docs/screenshots/admin-ai-friend.webp" alt="AI 好友管理" width="440"><br><sub><b>AI 好友管理</b> · 用量统计、角色审核</sub></td>
  </tr>
</table>
</details>

## 两个值得一看的部分

- **萌战引擎**：男女分组独立赛程，全阶段按预设时间自动推进；禁赛、双禁赛复活、轮空、人数不足自动缩编、平票、冠军空缺等边界都有明确规则与测试覆盖。规则细节见[文档](https://n-zihan.github.io/anime-club-website/features/contest/)。
- **AI 虚拟好友**：模型池只放**免费模型**（失败自动轮换 + 冷却），全部不可用时回落站内 FAQ，代码里没有付费模型入口；用户可自定义角色人格并选择公开或仅自己可见。实现见[文档](https://n-zihan.github.io/anime-club-website/features/anime/)。

## 快速开始

```bash
git clone https://github.com/N-zihan/anime-club-website.git
cd anime-club-website
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

**演示模式（零配置，推荐先跑这个）**：

```bash
# Windows PowerShell
$env:TESTING = "1"; $env:DATABASE_URL = "sqlite:///:memory:"; python run.py
# bash
TESTING=1 DATABASE_URL=sqlite:///:memory: python run.py
```

打开 <http://localhost:5000>，用演示账号 `testuser` / `password123` 登录（含管理后台权限），数据随进程销毁。

> ⚠️ 演示账号与演示数据只在 **SQLite 库**上生成（v4.31.8 起强制校验：`TESTING=1` + 非 Vercel + SQLite 三条缺一不可）。本地 `.env` 的 `DATABASE_URL` 若指向线上库，起服务时务必显式覆盖成 `DATABASE_URL=sqlite:///:memory:`，否则演示数据会被拒绝写入（保护生产库）。

**测试**：

```bash
pytest -q          # 单元 / 集成测试
npx playwright test  # 端到端（需 5000 端口空闲）
```

需要连真实数据库时：在 Supabase 建库后填好 `.env` 的 `DATABASE_URL`，执行 `python init.py` 再 `python run.py`。
完整步骤（环境变量清单、Supabase 建库、初始化的顺序要求）见[文档 · 快速开始](https://n-zihan.github.io/anime-club-website/getting-started/)与[文档 · 数据库](https://n-zihan.github.io/anime-club-website/database/)。

## 部署

Vercel 导入仓库即可（仓库自带 `vercel.json`），环境变量照着 `.env.example` 填。

- **先对生产库跑 `python init.py`，再推代码上线**——顺序反了会 500（新代码查不存在的列）。
- **SMTP 必须配**：注册要邮箱验证码、找回密码要发重置链接，不配 `MAIL_USERNAME` / `MAIL_PASSWORD` 时新社员无法注册（已有账号登录不受影响）。
- 域名请**自己注册**并在 Vercel 里绑定**你自己的域名**；本仓库文档里出现的社团域名只是该社团已部署实例，不要照抄。
- 免费计划（Vercel + Supabase）足够小型社团使用。

详细部署步骤、部署后验证与回滚：[文档 · 平台部署](https://n-zihan.github.io/anime-club-website/deployment/)。

## 技术栈

| 类别 | 技术 |
|---|---|
| 后端 | Python 3.14 + Flask 3.1（应用工厂 + Blueprint） |
| ORM / 数据库 | SQLAlchemy 2.0 / PostgreSQL（Supabase；测试用 SQLite 内存库） |
| 文件存储 | Supabase Storage（头像 / 照片 / 赛事图） |
| 前端 | Jinja2 + 原生 JS + 单文件 `style.css`（深浅色主题、无前端框架） |
| 测试 | pytest（690+ 用例）+ Playwright（端到端） |
| 部署 | Vercel（Python Serverless，香港区） |
| 认证 / 安全 | Session + Werkzeug 密码哈希 / CSRFProtect、SSRF 防护、上传体积限制 |
| 外部数据 | Bangumi API（番剧百科）、AniNewsAPI（新闻）、阿里云机器翻译、SiliconFlow（AI 解说 + 免费模型） |
| PWA | Service Worker + Manifest（可安装到桌面） |

## 项目状态

- 已部署生产环境（站点仅社员可访问）
- 后端测试 **711 passed**；端到端 Playwright **71 passed / 4 skipped**（75 条用例，按环境跳过 4 条）
- CI 全绿：pytest + flake8、Playwright、Pylint、CodeQL、Docs（文档站自动部署）
- 文档站：<https://n-zihan.github.io/anime-club-website/>

## 许可证

[MIT License](LICENSE) · 可自由使用、修改、分发，保留原始版权声明即可

*Made with ❤️ by 南平一中动漫社*
