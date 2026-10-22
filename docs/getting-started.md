# 快速开始（本地跑起来）

## 1. 环境要求

| 依赖 | 版本 | 说明 |
|---|---|---|
| Python | 3.14 | 与 Vercel 运行时一致（`vercel.json` 里写死 `pythonVersion: 3.14`） |
| Node.js | 20+ | 只跑 Playwright 端到端测试时需要 |
| PostgreSQL / Supabase | 任意 | 本地开发可以完全不用，直接用 SQLite 内存库 |
| 依赖清单 | `requirements.txt` | 生产依赖；测试需要额外装 `pytest`、`playwright` |

```bash
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate

pip install -r requirements.txt
pip install pytest
npm ci
npx playwright install chromium
```

## 2. 环境变量

复制模板并填空：

```bash
cp .env.example .env
```

**最小可运行配置**（只想把页面跑起来）：

```ini
SECRET_KEY=随便写一串长随机字符串
DATABASE_URL=sqlite:///app.db        # 或直接留空，用 TESTING 模式的内存库
CLUB_NAME=南平一中动漫社
GROUP_VERIFICATION_CODE=test_group_code
```

完整变量说明见 [平台部署 · 环境变量](deployment.md)。没有配置的可选能力会自动降级：

- 没配 `OPENAI_API_KEY` → AI 解说 / AI 好友不可用（AI 好友会回落到 FAQ）
- 没配 `ALIBABA_CLOUD_ACCESS_KEY_ID` → 新闻不翻译，详情页会显式提示"当前显示的是原文"
- 没配 SMTP → 验证码与找回密码链接只写日志、不发邮件

## 3. 启动（两种模式）

### 方式 A：演示模式（推荐，零配置）

```bash
# Windows PowerShell
$env:TESTING = "1"
$env:DATABASE_URL = "sqlite:///:memory:"
python run.py

# bash
TESTING=1 DATABASE_URL=sqlite:///:memory: python run.py
```

启动后访问 <http://localhost:5000>，会自动播种：

| 演示数据 | 内容 |
|---|---|
| 演示账号 | `testuser` / `password123`（同时是站长 + 运营，能进管理后台） |
| 演示成员 | 夏目 / 星野 / 千夏 / 桐谷 / 柚子 / 阿澈（密码同上，加入时间各不相同） |
| 演示赛事 | 「测试赛事」id=1，男女各 5 个候选角色 |
| 演示活动 | 4 场（放映会 / 招新 / 同人绘展 / 萌王决赛夜） |

> 内存库随进程结束即销毁，重启就回到干净状态，适合截图和演示。

### 方式 B：连真实数据库

```ini
DATABASE_URL=postgresql://user:password@host:6543/postgres
```

然后初始化表结构（**幂等，只加不删**）：

```bash
python init.py
```

### 登录页

![登录页](screenshots/login.webp)

## 4. 跑测试

```bash
# 全量单元/集成测试（约 1 分钟，500+ 用例）
python -m pytest -q

# 端到端测试（会自动启动本地服务，要求 5000 端口空闲）
npx playwright test
```

测试约定：

- **测试不允许依赖本机 `.env` 或外部 API**。需要环境变量的用例用 `monkeypatch.setenv` 自带假值。
  （踩过坑：AI 好友测试曾依赖本机 `OPENAI_API_KEY`，导致 CI 上 4 个用例必挂。）
- Playwright 的 `webServer` 用 `python run.py` 启动，`TESTING=1` 时自动播种演示数据。
- 跑 e2e 前先确认 5000 端口没被别的开发服务占着。

![社员首页](screenshots/home.webp)

## 5. 目录速览

```text
anime-club-website/
├── app/
│   ├── __init__.py        # create_app 工厂、蓝图注册、全局守卫（访客拦截）
│   ├── models.py          # SQLAlchemy 模型（15 张表）
│   ├── public.py          # 首页/简介/活动/资源/新闻/番剧百科/论坛/赛事页
│   ├── auth.py            # 注册（群验证码）/登录/找回密码/邮件验证码
│   ├── user.py            # 个人主页/通知/设置/头像/AI 好友
│   ├── admin.py           # 管理后台（用户/帖子/赛事/照片/活动/通知/AI 人设）
│   ├── contest_engine.py  # 萌战状态机（提名→海选→小组赛→淘汰赛→决赛）
│   ├── ai.py              # 萌战 AI 解说/预测（缓存、超时）
│   ├── ai_friend.py       # AI 虚拟好友（免费模型池 + FAQ 兜底）
│   ├── anime_news.py      # 新闻聚合 + 机器翻译
│   ├── bangumi.py         # Bangumi 番剧百科
│   ├── notify.py          # 站内通知 + 邮件
│   └── utils.py           # Supabase、图片压缩、SSRF 防护、get_or_404
├── templates/             # Jinja2 模板（base.html 内含导航抽屉/看板娘/主题）
├── static/                # style.css（单文件）、JS、图标
├── docs/                  # 项目文档（本目录）+ 截图
├── tests/                 # pytest + e2e
├── init.py                # 幂等迁移脚本（create_all + ensure_columns + buckets）
├── run.py                 # 入口（Vercel 直接用；TESTING 播种；INIT_DB 开关）
└── vercel.json            # 部署配置（hkg1 / python 3.14 / maxDuration 60）
```

## 下一步

- 搭数据库 → [数据库（Supabase）](database.md)
- 上生产 → [平台部署（Vercel）](deployment.md)
