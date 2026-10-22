<div align="center">

# 动漫社网站

[![Vercel](https://img.shields.io/badge/Deployments-Vercel-000000?logo=vercel)](https://www.nanyi-anime-club.top)
[![Python](https://img.shields.io/badge/Python-3.14-3776AB?logo=python)](https://python.org)
[![Flask](https://img.shields.io/badge/Flask-3.1-000000?logo=flask)](https://flask.palletsprojects.com)
[![Supabase](https://img.shields.io/badge/Supabase-PostgreSQL-3ECF8E?logo=supabase)](https://supabase.com)
[![Python application](https://github.com/N-zihan/anime-club-website/actions/workflows/python-app.yml/badge.svg)](https://github.com/N-zihan/anime-club-website/actions/workflows/python-app.yml)
[![Pylint](https://github.com/N-zihan/anime-club-website/actions/workflows/pylint.yml/badge.svg)](https://github.com/N-zihan/anime-club-website/actions/workflows/pylint.yml)
[![CodeQL Advanced](https://github.com/N-zihan/anime-club-website/actions/workflows/codeql.yml/badge.svg)](https://github.com/N-zihan/anime-club-website/actions/workflows/codeql.yml)
[![Playwright Tests](https://github.com/N-zihan/anime-club-website/actions/workflows/node.js.yml/badge.svg)](https://github.com/N-zihan/anime-club-website/actions/workflows/node.js.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Version](https://img.shields.io/github/v/tag/N-zihan/anime-club-website?label=Version)](https://github.com/N-zihan/anime-club-website/tags)

> 建站即用 · 开箱即改

动漫社网站，集社团展示、互动交流、资源分享、动漫资讯、线上赛事于一体

***在线访问***：[www.nanyi-anime-club.top](https://www.nanyi-anime-club.top)

</div>

## 功能概览

| 功能     | 说明                                                                         |
|----------|------------------------------------------------------------------------------|
| 用户系统 | 注册、登录、邮箱绑定、密码重置、头像上传                                     |
| 社员论坛 | 主题帖 + 楼层回复（支持 @某楼），帖子页底部固定输入框，无需滚动即可回复       |
| 番剧资源 | 社员推荐 → 管理员审核 → 公开显示                                             |
| 番剧百科 | Bangumi API 拉取番剧条目，支持分页浏览与搜索                                 |
| 动漫新闻 | 自部署 AniNewsAPI 聚合资讯，全文阅读、机器翻译（译文优先/仅原文/双语对照）、视频嵌入提取 |
| 站内搜索 | 关键词搜索番剧/新闻/资源，搜索历史本地保存，支持一键回填与清空               |
| 照片墙   | 活动照片分类展示，存储于 Supabase Storage                                    |
| 萌战系统 | 线上赛事引擎：提名 → 海选 → 小组赛 → 淘汰赛 → 冠军，内置禁赛/轮空/复活赛守则 |
| 通知中心 | 回复 / 审核 / 系统通知，可按类型静音、关闭未读红点轮询、开关邮件提醒         |
| 站点设置 | 主题 / 字号 / 动效、新闻显示语言、本机数据清理（本机偏好免登录可用）         |
| 管理后台 | 站长 / 运营双角色权限分离                                                    |

## 萌战系统

> 社团内部赛事引擎，男女分组独立比赛，全阶段按预设时间自动推进

| 阶段                                           | 赛制                                           | 晋级                          |
|------------------------------------------------|------------------------------------------------|-------------------------------|
| **提名期**（5天）                              | 每人每组提名 5 个角色                          | 进入海选池                    |
| **审核期**（3天）                              | 管理员进行角色审核                             | 准许进行海选                  |
| **海选**（4天投票+1天公示）                    | 每人每组 15 票，最多投 5 人，单角色 ≤ 3 票     | 男女各前 32 名，自动分组      |
| **小组赛**（3轮×4天投票+1天公示）              | 8 组 × 4 人，单循环三轮积分制，胜 3 分平 1 分  | 每组前 2 名，共 16 人         |
| **淘汰赛**（3轮×4天投票+1天公示）              | 16 强 → 8 强 → 4 强，单场定胜负                | 逐轮晋级                      |
| **决赛**（4天投票+3天公示）                    | 男女组各决一场                                 | 冠军 + 最终排名               |

### 赛事守则

- **禁赛**：禁赛角色不能被投票、不能晋级、已排对阵中自动判负（对手不战而胜）；前台全程显示禁赛徽章，由管理员在后台随时执行
- **双禁赛**：同一场双方均禁赛时本场无晋级名额，由该轮**票数最高的合法落败者替补复活**（复活赛赛制），保证下一轮人数链不断
- **轮空**：对阵为奇数或缺员时自动生成轮空场，一方不战而胜直接晋级
- **人数不足**：海选出线不足 32 人时自动缩编组数（最少 1 组），缺额以轮空槽占位，不会生成空组或抛错
- **平票处理**：小组赛按积分 → 总票数排序；淘汰赛平票时比较海选总票数
- **冠军空缺**：决赛双方均无法完赛时该届冠军空缺，仅公布其后排名
- 所有特殊状态（禁赛、轮空、替补晋级、无人晋级）均在投票页与公示页实时展示

## 技术栈

| 类别     | 技术                                      |
|----------|-------------------------------------------|
| 后端框架 | Python 3.14 + Flask 3.1                   |
| ORM      | SQLAlchemy 2.0                            |
| 数据库   | PostgreSQL(Supabase)                      |
| 文件存储 | Supabase Storage                          |
| 图片处理 | Pillow                                    |
| 前端     | HTML + CSS + Jinja2                       |
| 测试     | Pytest + Playwright                       |
| 部署     | Vercel                                    |
| 认证     | Session-based + Werkzeug 密码哈希         |
| 安全     | CSRFProtect(Flask-WTF)                    |
| 数据源   | Bangumi API · AniNewsAPI(自部署)          |
| 机器翻译 | 阿里云机器翻译（新闻正文）                |
| AI 解说  | SiliconFlow API(DeepSeek-V3)              |
| PWA      | Service Worker + Manifest（可安装至桌面） |

> AI 解说功能依赖 SiliconFlow API，需自行申请 API Key

## 项目结构

```text
动漫社网站/
├── .github/
│   └── workflows/                          # GitHub Actions CI/CD 工作流配置
│       └── ...
├── app/                                    # 应用核心代码
│   ├── __init__.py                         # Flask 应用工厂
│   ├── admin.py                            # 管理后台（站长/运营双角色）
│   ├── ai.py                               # AI 解说与预测
│   ├── anime_news.py                       # 动漫新闻聚合与机器翻译
│   ├── auth.py                             # 用户认证
│   ├── bangumi.py                          # 番剧百科（Bangumi API）
│   ├── changelog.py                        # github日志更新
│   ├── config.py                           # 赛事全局配置
│   ├── contest_engine.py                   # 萌战引擎
│   ├── models.py                           # 数据库模型
│   ├── notify.py                           # 通知模块
│   ├── preferences.py                      # 用户偏好读写（通知免打扰等）
│   ├── public.py                           # 公共页面路由（含论坛/新闻/设置）
│   ├── user.py                             # 用户中心
│   ├── utils.py                            # 工具函数
│   ├── version.py                          # 版本号解析（VERSION 文件 / git tag）
│   └── video_proxy.py                      # 视频代理（可选，按需启用）
├── static/                                 # 静态资源
│   ├── avatars/
│   │   └── default.png                     # 默认头像
│   ├── css/
│   │   └── style.css                       # 全局样式
│   ├── images/
│   │   ├── icon-192.png                    # PWA 图标（192x192）
│   │   ├── icon-512.png                    # PWA 图标（512x512）
│   │   └── kanban.png                      # 看板娘立绘
│   ├── js/
│   │   └── upload-compress.js              # 上传前图片压缩
│   ├── favicon.ico                         # 浏览器标签页图标
│   ├── robots.txt                          # 搜索引擎爬虫抓取规则
│   └── sitemap.xml                         # 站点地图
├── templates/                              # Jinja2 前端模板
│   └── ...
├── tests/                                  # Pytest 测试套件
│   ├── ...
│   └── e2e/                                # Playwright 测试套件
│       └── ...
├── .env.example                            # 环境变量模板（填写好后删除 .example 后缀使用）
├── .gitignore                              # Git 忽略规则
├── .python-version                         # Python 版本指定
├── .vercelignore                           # Vercel 忽略规则（不上传 .git 等）
├── VERSION                                 # 当前版本号（随 git tag 同步）
├── init.py                                 # 手动运维文件
├── LICENSE                                 # MIT 许可证
├── package.json                            # Node.js 构建配置
├── package-lock.json                       # Node.js 依赖锁定
├── playwright.config.js                    # Playwright E2E 配置
├── pytest.ini                              # Pytest 配置
├── README.md                               # 项目说明文档
├── requirements.txt                        # Python 依赖列表
├── run.py                                  # 应用启动入口
├── sw.js                                   # Service Worker
└── vercel.json                             # Vercel 部署配置
```
> /static 文件夹中有 favicon.ico、icon-192.png、icon-512.png
> 
> 这些是我为我的社团放的预览图，你可以换成你喜欢的

## 快速开始

```bash
git clone https://github.com/N-zihan/anime-club-website.git
cd anime-club-website
python -m venv .venv
source .venv/bin/activate      # Linux/Mac
# .venv\Scripts\activate       # Windows
pip install -r requirements.txt
```

创建 .env 文件:

```env
DATABASE_URL=postgresql://... # 从Supabase获取
GROUP_VERIFICATION_CODE=社团群号
SUPABASE_URL=https://...
SUPABASE_KEY=Settings → API → service_role secret # 仅供后端
SECRET_KEY=自创一个密码
SUPABASE_ANON_KEY=Settings → API → anon public # 用于前端
CLUB_NAME=社团名字 # 默认 动漫社
GITHUB_REPO= #你的仓库地址：username/reponame
GITHUB_TOKEN= # ghp秘钥

# AI模型配置
OPENAI_API_KEY=sk-... # 自行配置
OPENAI_BASE_URL=https://api.siliconflow.cn/v1
AI_MODEL=deepseek-ai/DeepSeek-V3
AI_CACHE_ENABLED=true
AI_CACHE_TTL=3600

# SMTP 邮件配置(用于发送验证码和重置链接以及通知)
SMTP_HOST=smtp.qq.com # 默认smtp.qq.com
SMTP_PORT=465 # 默认465
SMTP_USE_SSL=true # 默认true开启
MAIL_USERNAME=你的邮箱 # 建议额外注册一个
MAIL_PASSWORD=SMTP邮箱授权码

# 可选配置
SITE_URL= # 站点公网地址，用于通知里的链接（默认 https://www.nanyi-anime-club.top）
ALIBABA_CLOUD_ACCESS_KEY_ID= # 阿里云 AccessKey，用于新闻正文机器翻译
ALIBABA_CLOUD_ACCESS_KEY_SECRET= # 阿里云 AccessKey Secret，不配置则新闻不翻译
```

> API_key从[siliconflow](https://cloud.siliconflow.cn)获取
 
### Supabase：创建数据库

1. 访问 [Supabase](https://supabase.com) 注册账号，点击 **New project**
2. 填写项目名称、设置数据库密码，区域推荐选择 **Mumbai** 或 **Singapore**（国内访问更快）
3. 等待项目创建完成（约 2-3 分钟）

### 获取连接信息

项目创建完成后，在 Dashboard 页面顶部点击 **Connect** 按钮，会弹出一个连接面板，先选择 **Direct Connection string**
，里面包含了所有你需要的信息：

- **Transaction pooler（推荐）**：端口 `6543`，适用于 Vercel 等 Serverless 环境，支持最多 200 个并发连接
- **Direct connection**：端口 `5432`，直连数据库，限额 60 个连接

在 **Connection string** 区域找到 `URI` 格式的连接串，复制后替换密码即可得到 `DATABASE_URL`。

> 推荐使用 Transaction pooler（端口 6543），避免 Vercel 函数并发时占用过多数据库连接。

### 获取 Project URL 和 API Keys

1. 进入项目 Dashboard
2. 左侧菜单点击 **Integrations**
3. 选择 **Data API**
4. 在 Overview 页面即可看到 **API URL**（这就是 Project URL）

格式为：`https://xxxxxxxxxxxxx.supabase.co`

API Keys 在同一页面的 **Project API keys** 区域获取：

- **`anon` public**（或新版项目的 `Publishable key`）→ 填到 `SUPABASE_ANON_KEY`
- **`service_role` secret**（或新版项目的 `Secret key`）→ 填到 `SUPABASE_KEY`

> `service_role` 密钥权限极高，仅供后端使用，切勿泄露或提交到代码仓库。

运行：

```bash
python init.py
python run.py
```

访问 http://127.0.0.1:5000

测试：

```bash
# 后端测试
pytest -v

# 前端 E2E 测试
npm test
```

## 部署准备

### Vercel：部署网站

1. 创建仓库，推送代码至你的仓库
2. 访问 [Vercel](https://vercel.com)，用 GitHub 登录
3. 点击 **Add New → Project**，选择你的仓库导入
4. 在 **Environment Variables** 中添加环境变量（参考上面的 `.env` 模板）
5. 点击 **Deploy**，等待 1-2 分钟即可

> Vercel 会自动识别 Flask 应用，无需额外配置。部署后可以在侧边菜单栏 Environment Variables 中随时增删改环境变量

> 域名请前往域名供应商注册，推荐前往 [NameSilo](https://www.namesilo.com)（海外供应商，注册无需备案，若需备案，vercel不再适用，请使用国内服务器）
>
> 如果暂时不打算部署到公网，也可以只在本地运行，跳过这一步。run.py 启动后访问 http://127.0.0.1:5000 即可

> Supabase 和 Vercel 的免费计划足够支撑小型社团网站，赛事期间不会产生额外费用

## 项目状态

- 核心功能完整，已部署至生产环境（在线访问：https://www.nanyi-anime-club.top）
- 萌战引擎：禁赛 / 轮空 / 复活赛 / 冠军空缺等赛事守则完备，人数短缺全链路不崩
- 论坛：主题帖列表（按最后活跃排序、分页）、发帖校验、楼层回复与底部固定输入框
- 设置页：外观与显示、语言与翻译、通知免打扰、数据与隐私（本机偏好 + 账号级通知偏好）
- 后端测试：474 个用例全部通过
- 前端测试：Playwright 9 个 E2E 套件（登录、论坛、设置、新闻语言、内容、搜索历史、萌战、投票、管理后台），53 passed / 4 skipped
- 依赖安全：pip-audit 与 npm audit 均无已知漏洞
- CI：GitHub Actions（pytest + flake8 + CodeQL + Playwright）

## 许可证

本项目采用 MIT License 开源

你可以自由地使用、修改、分发本项目的代码，但需保留原始版权声明。详情请见 LICENSE 文件

*Made with ❤️ by 南平一中动漫社*
