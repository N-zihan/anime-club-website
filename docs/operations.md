# 运维手册

## 1. 版本号与发布流程

版本号遵循 SemVer：

| 位 | 什么时候加 | 例子 |
|---|---|---|
| MAJOR | 破坏性变更（URL/行为/数据不兼容） | 大改版 |
| MINOR | 新增用户可见功能 | 论坛、AI 好友、页面补全 |
| PATCH | 修 Bug / 样式 / 依赖 / 文档 / CI | 复选框样式、测试修复 |

发布步骤（每次都一样）：

```bash
# 1) 改 VERSION 文件（无 BOM、LF 换行！）
echo "v4.24.0" > VERSION

# 2) 提交
git add -A
git commit -m "feat: xxx（v4.24.0）"
git push origin main

# 3) 打标签并推送（version.yml 会把 VERSION 同步成 tag 版本）
git tag -a v4.24.0 -m "v4.24.0：xxx"
git push origin v4.24.0

# 4) 验证
git ls-remote origin refs/heads/main refs/tags/v4.24.0
```

!!! tip "发行版策略"
    `release.yml` **只对 `vX.Y.0` 自动创建 GitHub Release**，补丁版本只打 tag 不发 Release
    （避免发行版太频繁、刷屏活动流）。需要为某个补丁版补发 Release 时，去
    Actions → Release → Run workflow 手动指定 tag 即可。

## 2. 上线 checklist

- [ ] 本地 `pytest -q` 全绿
- [ ] `npx playwright test` 全绿（或至少跑改动模块相关用例）
- [ ] 如果改了 `models.py`：**先在本地对生产库跑 `python init.py`**，再推代码
- [ ] 改了前端资源：确认 `VERSION` 变了（`style.css?v=` 才会刷新缓存）
- [ ] 推送后验证版本号与公开页状态码（命令见 [部署验证](deployment.md)）
- [ ] 涉及权限/支付/AI 的改动，用一个普通社员账号回归一遍

## 3. 备份策略

| 对象 | 频率 | 方式 |
|---|---|---|
| PostgreSQL | 每天（Supabase 自动） | 控制台 Backups；重要改动前手动 `pg_dump` |
| Storage（照片/头像） | 每月或大活动后 | Supabase 控制台导出 |
| 代码 | 每次提交 | GitHub（tag 即版本快照） |
| 环境变量 | 变更时 | 存在密码管理器；`requirements.txt` 里只放依赖不放密钥 |

## 4. 日常监控

- **版本**：`/settings` 页脚显示当前版本，部署后先看这里。
- **错误**：Vercel 项目 → Logs（函数日志）查 500 堆栈。
- **数据库**：Supabase Dashboard 的 Query Performance / Connections；连接数快满时检查是否有长事务。
- **AI 成本**：AI 好友只走免费模型，成本为 0；萌战解说走付费模型但带 1 小时缓存，量大时先看缓存命中。
- **依赖安全**：`pip-audit -r requirements.txt`（Windows 下需 `$env:PYTHONUTF8=1`，否则 GBK 解码会炸）。

## 5. 故障排查速查

| 现象 | 先看哪里 | 常见原因 |
|---|---|---|
| 502/504 | Vercel Logs | 函数超时（>60s）、上游 API 无响应 |
| 500 | Vercel Logs | 缺环境变量、表/列不存在（迁移没跑） |
| 页面 302 到登录 | 预期行为 | 访客被守卫拦截；确认路由是否在白名单 |
| 登录成功但立刻掉线 | 环境变量 | `SECRET_KEY` 未配置或不稳定 |
| 上传失败 | 浏览器 Network | 413（超体积）、Supabase Key 错误、桶权限 |
| 图片不显示 | 浏览器 Console | Storage 桶未建/私有、URL 过期 |
| 新闻全英文 | 详情页提示条 | 阿里云翻译未配置/额度用尽 |
| AI 回复固定 FAQ 文案 | 后端日志 | 免费模型全挂/限流；看 `AI_CHAT_MODELS` |
| 部署后样式没变 | `/settings` 版本号 | 版本号没变 → 部署未完成；强刷 |
| 数据库连接超限 | Supabase | 用了直连而非 pooler；改 `6543` |

## 6. 密钥与安全

- `SECRET_KEY`、`DATABASE_URL`、`SUPABASE_KEY`、SMTP 密码、各类 API Key：**只放 Vercel 环境变量**。
- 怀疑泄露时：立刻在对应平台重置 → 更新 Vercel 变量 → 重新部署 → 检查日志异常访问。
- 管理员账号：至少保留两个 owner，避免单点失联。
- 站点是内部站，`robots.txt` 已禁止搜索引擎抓取内部页；不要为了"推广"把内部页面加进白名单。

## 7. 一次性运维脚本

| 脚本 | 用途 |
|---|---|
| `python init.py` | 幂等建表 / 补列 / 补热点索引 / 建桶 / 孤儿数据报告 |
| `INIT_DB=1 python run.py` | 只建缺失的表（不补列），本地或一次性使用 |
| `python run.py`（`TESTING=1`） | 演示模式，带种子数据 |

!!! note "限流与操作日志"
    - 登录失败计数、验证码发送节流走数据库表 `rate_limits`（跨 Serverless 实例共享），
      数据库不可用时自动回退进程内计数，不会把用户锁死。
    - 后台写操作写入 `admin_logs`，可在 `/admin/logs` 查看（分页，最近在前）。

## 8. 常见"历史坑"提醒

- Vercel 部署有时 10 分钟才生效，**不要**因为页面没变就反复改代码。
- 改 `VERSION` 时务必用 UTF-8 **无 BOM** 写入；带 BOM 会让版本号解析出不可见字符（页脚和 `style.css?v=` 会坏）。
- PowerShell 的 `Set-Content -Encoding UTF8` 在旧版本会写 BOM，改配置文件请用编辑器或 `write` 工具。
- Supabase 连接串里的密码需要 URL 编码。
