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
