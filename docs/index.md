# 南平一中动漫社 · 项目文档

这里是[南平一中动漫社官网](https://www.nanyi-anime-club.top)的完整文档：从本地跑起来、数据库结构、Vercel 部署，到站内每个功能模块的细节与运维流程。

> **站点性质**：社团内部站点，**仅社员登录访问、不对外开放注册**。因此文档里的所有截图都来自本地 `TESTING` 模式的演示数据（演示账号 / 演示帖子 / 演示活动），不含真实社员信息。

## 这套系统是什么

| 项目 | 说明 |
|---|---|
| 后端 | Flask 3.1（应用工厂 + Blueprint），SQLAlchemy 2.0 |
| 数据库 | PostgreSQL（生产用 Supabase），本地测试用 SQLite 内存库 |
| 前端 | Jinja2 模板 + 原生 JS + 单份 `style.css`（深浅色主题、无前端框架） |
| 部署 | Vercel（Python Serverless），香港区 `hkg1` |
| 测试 | pytest（500+ 用例）+ Playwright（端到端） |
| AI | 萌战解说 / 预测 + AI 虚拟好友（全部免费模型池，不产生付费调用） |

## 文档地图

<div class="grid cards" markdown>

- :material-rocket-launch: **[快速开始](getting-started.md)** —— 把项目在本地跑起来，以及测试怎么跑
- :material-database: **[数据库](database.md)** —— Supabase 建库、连接串、初始化脚本、表清单与踩坑
- :material-cloud-upload: **[平台部署](deployment.md)** —— Vercel 配置、环境变量、域名、部署验证与回滚
- :material-account-group: **[账号与权限](features/accounts.md)** —— 注册验证码、登录、找回密码、权限分级
- :material-forum: **[论坛 / 照片墙 / 通知 / 设置](features/community.md)** —— 社员互动模块的细节
- :material-television: **[番剧资源 / 百科 / 新闻 / AI 好友](features/anime.md)** —— 内容模块与 AI 功能
- :material-trophy: **[萌战系统](features/contest.md)** —— 提名 → 海选 → 小组赛 → 淘汰赛的完整规则与实现
- :material-shield-account: **[管理后台](features/admin.md)** —— 审核、赛事管理、用户管理、数据导出
- :material-wrench: **[运维手册](operations.md)** —— 版本发布、备份、故障排查
- :material-bug: **[开发约定与踩坑](dev-notes.md)** —— 测试要求、截图流程、已知环境坑
- :material-help-circle: **[常见问题](faq.md)** —— 访问、部署、数据库、功能、开发、发布的高频疑问

</div>

## 站点截图（演示数据）

<div class="grid cards" markdown>

- ![着陆页](screenshots/splash.webp)

- ![登录页](screenshots/login.webp)

- ![社员首页](screenshots/home.webp)

- ![萌战赛事详情](screenshots/contest-detail.webp)

</div>

## 相关链接

- 线上站点：<https://www.nanyi-anime-club.top>（仅社员可登录）
- 代码仓库：<https://github.com/N-zihan/anime-club-website>
- README / 功能导览：仓库根目录 [`README.md`](https://github.com/N-zihan/anime-club-website#readme)
