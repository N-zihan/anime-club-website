# 开发约定与踩坑

## 1. 提交前必须过

```bash
python -m pytest -q          # 单元/集成（500+ 用例）
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

## 6. 技术债与重构路线

### 现状（2026-10 量化）

| 指标 | 数值 | 备注 |
|---|---|---|
| `app/public.py` | 约 1950 行 | 已抽走离线路由/RSS/实时票数（`public_misc.py`）；目标 < 800 行 |
| `templates/base.html` | 约 730 行（原 1127） | 已把看板娘 JS、内联样式外置 |
| `static/css/style.css` | 约 3500 行 | 单文件，按注释分节；暂不做构建流程，够用 |
| 测试覆盖率 | 66% → 约 67% | `python -m pytest --cov=app --cov-report=term-missing` |
| 低覆盖模块 | `changelog` 49%（原 0%）、`bangumi` 9%、`auth` 52%、`utils` 58%、`public` 60%、`admin` 61% | 这些是下一步补测试的重点 |

### 已还的债

- **时间口径收口**：`app/utils.py` 提供 `cn_now()` / `to_cn()`，替换了各模块里手写的
  `datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=8)`；时区错一次就是整站时间错 8 小时。
- **base.html 瘦身**：看板娘脚本抽到 `static/js/kanban.js`，内联样式并入 `style.css`。
  （改 base.html 前先跑 `npx playwright test tests/e2e/board.spec.js` 确认导航/看板娘正常）

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
