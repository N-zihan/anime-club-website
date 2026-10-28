# 开发约定与踩坑

## 1. 提交前必须过

```bash
python -m pytest -q          # 单元/集成（690+ 用例）
npx playwright test          # 端到端（改到 UI 时必跑）
```

约定：

- **测试不允许依赖本机 `.env` 或外部网络**。需要环境变量的用 `monkeypatch.setenv` 自带假值；
  需要外部 API 的一律 mock。
  （真实事故：AI 好友的 4 个用例依赖本机 `OPENAI_API_KEY`，CI 上必挂。）
- 新功能至少配两条测试：正常路径 + 越权/边界路径。
- 修 Bug 先写能复现的测试，再修。
- 改模板/样式后补一条 e2e 断言，防止下次回归（例如"勾选框宽度 < 40px"）。

## 2. 代码风格

- 后端 Python 3.14，4 空格缩进，函数写 docstring（中文可以），命名 `snake_case`。
- 模板 Jinja2：`templates/*.html`，继承 `base.html`；样式统一进 `static/css/style.css`（单文件，按 section 注释分块）。
- 前端 JS 尽量原生，不引框架；DOM 操作集中在一个 IIFE 里，避免全局污染。
- 不要为了"炫技"引入新依赖：依赖越少，部署越稳、审计越省事。

## 3. 已知环境坑（都踩过）

### 3.1 两个 Python 3.14

Windows 开发机上可能同时有 `py`（pythoncore）和 `C:\Python314\python.exe` 两个解释器。
Playwright 的 `webServer` 用后者启动，**依赖要装两套**，否则 e2e 会报 `ModuleNotFoundError: werkzeug`：

```powershell
py -m pip install -r requirements.txt
C:\Python314\python.exe -m pip install -r requirements.txt
```

### 3.2 PowerShell 写文件的编码坑

- `Set-Content -Encoding UTF8`（Windows PowerShell 5.1）会写 **BOM**；
  改 `VERSION`、`.env`、YAML 这类文件请用编辑器/`write` 工具。
- 用 `Get-Content -Raw` 读 UTF-8 中文再写回，会按系统 ANSI 解码 → 中文变乱码。
  **不要**用 PowerShell 做"读-改-写"；用 `edit` 工具或 Python 脚本（显式 `encoding='utf-8'`）。
- 控制台是 GBK，`py -c` 里带中文/嵌套引号很容易炸；写临时 `.py` 文件再执行更稳。

### 3.3 内存 SQLite 的 ResourceWarning

`TESTING=1` 用 `sqlite:///:memory:`，Python 3.14 在 GC 时会刷 `ResourceWarning: unclosed database`。
fixture 已显式 `db.engine.dispose()`，`pytest.ini` 也只静音这一类警告——它不是功能缺陷。

### 3.4 Playwright 相关

- 跑 e2e 前确认 **5000 端口空闲**；残留的开发服务会让 Playwright 连到旧代码（曾出现"测试还看到旧页面"）。
- 新手引导遮罩会在部分页面自动弹出并拦截点击；测试里统一在 `beforeEach` 预置
  `onboarding_done_*` 标记，或在首页额外等待。
- 表单提交按钮选择器要写具体（如 `.forum-form-actions button[type="submit"]`），
  否则可能命中抽屉里隐藏的「退出登录」按钮。

### 3.5 其他

- 模板里 `{% block %}` 的编辑容易把换行/缩进改坏，改完务必读一遍渲染结果或跑页面测试。
- 通知红点轮询是异步的：断言"清零"前要等设置保存完成、清空已捕获请求再检查。
- Vercel 部署有延迟，验证请用 `/settings` 里的**版本号**，不要用"页面看起来没变"。
- 数据库迁移顺序：**先 `init.py` 后推代码**，反了必 500。

### 3.6 本地 `.env` 指向生产库时，`TESTING=1` 会把演示数据灌进线上

- 事故（v4.31.8 修复）：本地 `.env` 的 `DATABASE_URL` 指向 Supabase 生产库，
  而 `run.py` 里"测试数据初始化"（测试赛事 / 演示成员 / 演示活动 / 演示 AI 人设）
  是**模块级**执行的。只要用 `TESTING=1 python run.py` 起服务，这些演示数据就会
  直接写进生产库 —— 表现就是「测试赛事自己上线了」，而且还会多出一个密码是
  `password123` 的 **owner 账号**（`testuser`），属于安全漏洞。
- 现在的规则：`app/utils.py` 的 `demo_seed_allowed()` 强制要求
  **TESTING=1 + 非 Vercel + SQLite 库**，三条缺一不可；否则只打印拒绝提示，不写库。
- 本地要跑演示环境，**显式覆盖数据库**，别复用 `.env` 里的生产连接：
  ```powershell
  $env:TESTING='1'; $env:DATABASE_URL='sqlite:///:memory:'; py run.py
  ```
- 排查线上脏数据可以直接用 `.env` 的 `DATABASE_URL` 连库查（只读），
  但删除前先确认不是真实社员数据。

## 4. 截图规范（文档/README 用）

- 来源：本地 `TESTING=1` 演示环境，**不出现真实社员信息**。
- 浏览器视口 1440×900，整页截图后用 Pillow 缩到宽 1200，存 WebP（quality 82）。
- 命名与 README/文档中的引用路径一致：`docs/screenshots/<name>.webp`。
- 文档里引用图片用相对路径，例如 `![论坛](screenshots/forum.webp)`。

## 5. 文档站维护

- 文档就是本目录的 Markdown，改完提交，GitHub Actions 的 `docs.yml` 会自动构建并部署到 GitHub Pages。
- 本地预览：

```bash
pip install mkdocs-material
mkdocs serve            # http://127.0.0.1:8000
mkdocs build            # 输出到 site/
```

- 新增页面记得在 `mkdocs.yml` 的 `nav` 里挂上，否则侧边栏看不到。
- **文档里不写真实线上域名**：一律用占位符 `your-site.example.com` / `<你的域名>`，
  `tests/test_docs_guard.py` 会卡这条。
- **部署文档必须和仓库一致**：`vercel.json`、`.env.example` 改了要同步更新 [平台部署](deployment.md)，
  不允许写仓库里不存在的配置项、流程或数字。
- 文档里的测试数、命令、文件路径要和仓库现状一致；历史数字只放在「技术债 / 批次回顾」里并标注版本。

## 6. 技术债与重构路线

### 现状（2026-10 量化）

| 指标 | 数值 | 备注 |
|---|---|---|
| `app/public.py` | **595 行**（原约 2000） | 已拆出 `public_contest.py`（1051）、`public_board.py`（351）、`public_misc.py`（约 180）、`public_bp.py` |
| `templates/base.html` | 约 730 行（原 1127） | 已把看板娘 JS、内联样式外置 |
| 模板内联样式 | **817 → 198 处**（v4.33.1） | 620 处静态样式提取成 `style.css` 的 `.u-*` 工具类；剩下 122 处含 `display`（JS 要读初始值，故意保留）、16 处是动态 Jinja 值 |
| `static/css/style.css` | 约 4600 行（含 310 个自动提取的 `.u-*` 工具类） | 单文件，按注释分节；暂不做构建流程，够用 |
| 测试覆盖率 | **71%**（原 66%），CI 门槛 **70%** | `python -m pytest --cov=app --cov-report=term-missing` |
| 低覆盖模块 | `changelog` 49%、`public_contest` 55%、`public` 56%、`admin` 61%、`throttle` 68%、`user` 69% | 高覆盖：`utils` 82%、`bangumi` 90%、`models` 93% |

### 已还的债

- **时间口径收口**：`app/utils.py` 提供 `cn_now()` / `to_cn()`，替换了各模块里手写的
  `datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=8)`；时区错一次就是整站时间错 8 小时。
- **base.html 瘦身**：看板娘脚本抽到 `static/js/kanban.js`，内联样式并入 `style.css`。
  （改 base.html 前先跑 `npx playwright test tests/e2e/board.spec.js` 确认导航/看板娘正常）

### 数据库迁移：为什么暂时不引入 Alembic（2026-10 评估结论）

- **现状**：`init.py` 做三件事——`db.create_all()` 建缺失的表、`ensure_columns()` 只加列、
  `ensure_indexes()` 补索引；没有 migration 版本表，也没有 downgrade。
- **风险**：改列名 / 改类型 / 删列只能手写 SQL；回滚依赖数据库备份；多人在不同分支改
  `models.py` 时容易出现「线上列和代码不一致」。
- **候选方案**：
  1. **Alembic / Flask-Migrate**：标准做法，但要多两个依赖 + 生成/审阅 migration 的流程；
     本项目跑在 Vercel Serverless，迁移必须在本地手动执行（不能靠冷启动自动跑），
     而且 Supabase pooler（6543 事务模式）上跑 DDL 偶有锁/连接问题，收益有限。
  2. **保持 `init.py` + 手工 SQL**（当前）：零依赖、幂等、适合小表少变更的场景。
  3. **轻量版本表**：只在 `init.py` 里记录已执行脚本编号。为 20 张表引入这套不划算。
- **结论**：**暂不引入 Alembic**。触发重新评估的条件：表数量明显变多 / 需要频繁改列类型 /
  多人同时改 schema。届时再上 Flask-Migrate，并保留 `init.py` 做「新环境一次到位」。
- **当前约定**（改表结构时必须遵守）：
  1. 先在本地跑一遍 `python init.py`，确认 `ensure_columns()` 输出符合预期；
  2. 生产改动前先备份（Supabase Dashboard 导出或 `pg_dump`）；
  3. 只加不删；确需删列/改类型时，手写 SQL 并记录到 [运维手册](operations.md)；
  4. 先在生产库跑 `init.py`，再推代码（反了必 500）。

### 第九批已还（v4.34.4 小组赛公示期徽章）

- **现象**：小组赛第 3 轮投票结束、进入 1 天公示期后，同一页里一部分候选人显示「淘汰赛」，
  另一部分显示「小组赛进行中」——用户看到的是「小组赛结束了状态还乱」。
- **根因**：晋级者在 `run_group_promotion()` 里被改成 `stage='knockout'`，未晋级者仍保留
  `stage='group_stage'`；而模板徽章只按 `stage` + `phase` 粗略判断，公示期（`group_round_3_result`）
  被算进「小组赛进行中」，于是出现两种标签并存。
- **修复**：模板徽章按阶段细分：公示期里 `stage=knockout` → 「晋级淘汰赛」，
  `stage=group_stage` → 「小组赛淘汰」；第 3 轮投票期内仍显示「小组赛进行中」。
- 生产库实测：`/contest/1` 渲染出 32×「晋级淘汰赛」+ 32×「小组赛淘汰」，0×「小组赛进行中」。
- 新增 2 条回归测试（`tests/test_contest_badges.py`）。
- **同页第二个 Bug（v4.34.5）**：小组赛投票页的「晋级名单（16强）/16强分组」全部显示「已移除」。
  根因是模板对 `candidates` 用了 `selectattr('id', ...)`，而路由传进来的 `candidates` 是
  `{id: Candidate}` **字典**，Jinja 遍历字典拿到的是 key（int）→ 永远匹配不到。
  改成 `candidates.get(id)`，并补 1 条模板守卫测试。全量 pytest **729 passed**。

### 第八批已还（v4.34.0 性能）

- **静态资源缓存**：所有 `/static/` 响应原来都是 `no-cache`，Vercel 上每个资源都要回源一次函数
  （实测 150~200ms/个）。现在带 `?v=<版本>` 的 CSS/JS 返回
  `Cache-Control: public, max-age=31536000, immutable`，无版本号的图片 1 天；
  `/robots.txt`、`/sitemap.xml`、`/manifest.json` 缓存 1 小时（爬虫友好）。
- **图片瘦身**：看板娘原图 1447×1447 / **536KB**，显示只有 120~180px → 生成 480×480
  WebP **31KB**（PNG 126KB 兜底，`<picture>` 自动选）；`favicon.ico` 之前其实是
  926×926 JPEG（130KB）→ 换成真正的多尺寸 ICO（9KB）；33 处 `<img>` 补 `loading="lazy"`。
- **少查询**：`/share/contest/<id>` 候选查询 5 → 3 次；`/about` 5 → 3 次；
  另外 v4.33.2 已把「帖子管理」的 N+1 改成 2~3 次。
- **测量方法**：`scripts` 之外用一次性脚本统计每个页面的 SQL 次数与耗时（`before_cursor_execute`
  事件）；本地直连生产库单次查询约 250ms（地理距离），Vercel `hkg1` → Supabase 孟买
  约 50~100ms/次，所以**减少查询次数**比优化单条 SQL 更划算。
- **下一步（需要主人决策/成本）**：Supabase 迁到新加坡/香港可再省 30~60ms/查询；
  匿名公开页上 Vercel 边缘缓存（现在 HTML 是 dynamic）；国内备案 + 双栈部署
  （`hkg1` 对国内用户已经比较近，主要是微信/QQ 卡片信任问题）。
- 全量 pytest **728 passed**。

### 第七批已还（v4.33.0 / v4.33.1）

- **周报只发了一部分人（真 Bug 修复）**：旧实现给每个收件人单独新建 SMTP 连接，100+ 人会在
  Vercel 函数 60 秒上限内被截断。现在所有邮件共用一条 SMTP 连接（`auth.send_html_batch`），
  单次最多 200 封 / 45 秒；发送结果记进新表 `digest_sends`（user_id + 当周周一），
  同一周只发一次；`vercel.json` 增加 01:30 UTC 的 `/api/cron/weekly-digest-resume` 自动续发。
- **安全响应头**：`X-Content-Type-Options` / `X-Frame-Options` / `Referrer-Policy` /
  `Permissions-Policy` 统一在 `after_request` 注入（CSP 仍由模板 meta 提供）。
- **PWA 图标修复**：`icon-192.png` / `icon-512.png` 实际是 **JPEG 改名**、且都是 926×926；
  重新生成真正的 PNG 192/512 + `icon-512-maskable.png`，manifest 按 `any` / `maskable` 分开声明。
- **健康检查 + 每周巡检**：`/api/health`（版本 + 数据库连通性）+ `patrol.yml`
  （每周一检查 health、公开页、robots、sitemap、分享图；域名走仓库变量 `SITE_URL`，不写进仓库）。
- **覆盖率门槛**：CI 的 pytest 加 `--cov-fail-under=70`（当前 71%）。
- **数据库迁移评估**：结论「暂不引入 Alembic」，见上一节，含改表时的 4 条约定。
- **内联样式清理**：817 → 198；620 处静态样式自动提取为 `style.css` 的 `.u-*` 工具类
  （选择器重复 4 次保持接近内联的优先级，不写 `!important`，JS 行内样式仍可覆盖）。
  清理后做了**computed style 前后对比**：22 个页面 / 3068 个元素，只有 7 个元素差异，
  且都是异步内容加载（AI 战报文字长度）或动画（实时红点 box-shadow）导致的假差异。
  剩下 122 处含 `display` 的内联样式是**故意保留**的：多处 JS 用 `el.style.display === 'none'`
  判断初始状态，挪进 class 会破坏折叠逻辑；16 处是 `{{ ... }}` 动态值。
- 全量 pytest **711 passed**。

### 第六批已还（v4.29.0）

- **萌战对阵图**：`/contest/<id>/bracket`，按 16强→8强→4强→决赛 分列，晋级方金色描边；
  新增 5 条测试。
- **邮件周报**：`app/digest.py` + cron 接口 `/api/cron/weekly-digest`（CRON_SECRET 鉴权、
  非周一跳过、`?force=1` 补发）+ `vercel.json` crons；新增 11 条测试。
- **路由级测试**：`tests/test_public_routes_extra.py` 17 条（基础页/404/规则页/提名校验/投票拦截/AI 接口）。
- **无障碍/性能收尾**：窄屏后台表格横向滚动、相册图片 `decoding=async`、
  contest_detail 20 处阶段横幅内联样式改为 `.phase-banner`。
- **video_* 死代码下线**：生产库三张空表已 DROP，models 同步移除。
- 全量 pytest **675 passed**。

### 第五批已还（v4.28.7）

- **补测试**：`tests/test_utils_extra.py` 30 条 + `tests/test_auth_helpers.py` 18 条；
  覆盖率 utils **58%→82%**、auth **52%→68%**，全量 pytest **642**、总覆盖率 **66%→71%**。
- **修掉一个潜在 Bug**：`_validate_file_content` 原依赖 `python-magic` + `imghdr`，
  但 imghdr 在 Python 3.13 已移除、magic 不在依赖里 → 生产环境该函数必抛异常，
  `validate_file_upload` 永远判失败。改用项目已有的 Pillow 真实解码校验，并补了测试。
- **死代码下线**：删除 `app/video_proxy.py` 及 `app/__init__.py` / `init.py` 的可选导入与注册逻辑
  （video_* 三张表保留，避免动生产数据；后续确认无数据再考虑删表）。

### 第四批已还（v4.28.5）

- **拆出 `public_contest.py`**：赛事中心/规则/详情、提名、海选/小组/淘汰赛投票与提交、
  票数接口、AI 解说与预测（1051 行）；`public.py` 降到 **595 行**（达成 <800 目标）。
  拆分时按 pytest 报错逐个补了 `threading` / `time` / `os` / `func` 导入，并把 3 处测试 patch
  目标从 `app.public` 改到 `app.public_contest`（AI 解说/预测、冷却机制）。
- **补测试**：`tests/test_bangumi.py` 18 条（mock requests，覆盖成功/失败/空响应/异常），
  bangumi 覆盖率 **9% → 90%**；全量 pytest **594 passed**。

### 第三批已还（v4.28.4）

- **拆出 `public_board.py`**：论坛列表/发帖/帖子详情/回复/编辑/删除/举报整块（424 行）独立，
  共用 `public_bp`，endpoint 名不变；全量 pytest 576 通过。
- **剩余拆分项**：`public_contest.py`（赛事公开路由，最大块）与 `public_news.py`，下一轮继续。

### 第二批已还（v4.28.3）

- **拆 `public.py` 第一刀**：`public` 蓝图对象抽到 `public_bp.py`；离线页 / RSS / 实时票数接口抽到
  `public_misc.py`（共用蓝图，endpoint 名不变）。public.py 约 1950 行。
- **阶段计算去重**：`contest_engine` 新增 `current_group_round()` / `current_knockout_sub_round()`，
  4 个投票路由 + `vote-stats` 接口统一使用。
- **补测试**：`tests/test_changelog.py`（10 条），changelog 覆盖率 0% → 49%。

### 待办（按性价比排序）

1. **继续拆 `app/public.py`**：按同样方式抽 `public_contest.py`（赛事公开路由）、`public_board.py`（论坛路由），
   目标是 public.py < 800 行（蓝图与 endpoint 已经共享，改名风险已消除）。
2. ~~赛事阶段计算去重~~（v4.28.3 已完成）
3. **补低覆盖模块测试**：`changelog`（纯函数，可 100%）、`bangumi`（mock HTTP）、`auth` 的验证码/重置密码分支。
4. **评估 migrations 框架**：目前 `init.py` 只加列不删改；长期改表结构时 Alembic 更稳，但要权衡 Vercel 冷启动成本。
5. **死代码确认**：`app/video_proxy.py` 相关系统当前未启用（`init.py` 里是幂等空操作），确认无用后连表一起下线。
6. **CSS/模板内联样式收敛**：`contest_detail.html` 等页面仍有大量 `style="..."`，逐步换成 class。

### 原则

- 重构必须**保持 endpoint 名与模板变量不变**，每次改完跑全量 pytest + 至少 board/admin e2e。
- 不为了"看起来整洁"引入构建链（Webpack/Vite）；本站是 Flask + 服务端渲染，简单即优势。
