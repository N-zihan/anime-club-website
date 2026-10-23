# 数据库（Supabase）

## 用的是什么

- **生产**：PostgreSQL，托管在 [Supabase](https://supabase.com)。Vercel 是无状态的 Serverless，数据库必须在外部。
- **本地测试**：`TESTING=1` 时用 SQLite 内存库（`sqlite:///:memory:`），随进程销毁。
- **ORM**：SQLAlchemy 2.0 + Flask-SQLAlchemy 3.1，没有 Alembic，迁移靠 `init.py`（幂等脚本）。

!!! warning "表名是单数，别用复数"
    生产库里有些表是**单数**命名：`user`、`message`、`reply`、`activity`、`photo`。
    而另外一些又是复数：`contests`、`candidates`、`contest_votes`、`nominations`、`notifications`。
    写裸 SQL 或排查问题时别想当然（踩过坑：本地检查代码写成 `users` → `NoSuchTableError`）。

## 1. 在 Supabase 建库（与 README 保持一致）

1. 访问 <https://supabase.com> 注册账号，点击 **New project**。
2. 填写项目名称、设置数据库密码；**区域推荐 Mumbai 或 Singapore**（国内访问更快）。
3. 等待项目创建完成（约 2–3 分钟）。
4. 项目创建后，在 Dashboard 顶部点击 **Connect** 按钮，弹出连接面板，先选择
   **Direct Connection string**，里面是完整的连接信息：
   - **Transaction pooler（推荐）**：端口 `6543`，适合 Vercel 等 Serverless 环境，支持最多 200 个并发连接。
   - **Direct connection**：端口 `5432`，直连数据库，限额 60 个连接。
5. 在 **Connection string** 区域找到 `URI` 格式的连接串，复制后把密码替换成你自己的，就是 `DATABASE_URL`：

```ini
DATABASE_URL=postgresql://postgres.<project-ref>:<password>@aws-0-<region>.pooler.supabase.com:6543/postgres
```

> 推荐用 Transaction pooler（6543），避免 Vercel 函数并发时把数据库连接占满。

!!! tip "密码里的特殊字符要 URL 编码"
    `@` `#` `/` `:` 等字符出现在密码里时，必须百分号编码，否则连接串会被解析错。

### 获取 Project URL 和 API Keys（Storage / 上传要用）

1. 进入项目 Dashboard → 左侧菜单 **Integrations**。
2. 选择 **Data API**，在 Overview 页面即可看到 **API URL**（这就是 Project URL）：

```ini
SUPABASE_URL=https://xxxxxxxxxxxxx.supabase.co
```

3. **Project API keys** 在同一页面：

| Supabase 里的名字 | 填到哪个变量 | 用途 |
|---|---|---|
| `anon` public（新版项目叫 `Publishable key`） | `SUPABASE_ANON_KEY` | 给前端用的公开 key |
| `service_role` secret（新版项目叫 `Secret key`） | `SUPABASE_KEY` | **仅后端使用**，权限极高 |

!!! danger "service_role 密钥等于数据库管理员"
    它不受行级权限限制，泄露等于数据库裸奔。只放后端环境变量，绝不进前端、不进仓库、不发聊天。

## 2. 初始化表结构（重要：顺序不能反）

```bash
# 在本地执行，连的是生产库（DATABASE_URL 指向 Supabase）
python init.py
```

`init.py` 做的事（幂等，重复跑安全）：

1. `db.create_all()` —— **只建缺失的表**，不改已有表
2. `ensure_columns()` —— 给已存在的表补缺失的列（只加不删）
3. `ensure_buckets()` —— 创建 Supabase Storage 桶：`photos`、`contest_images`、`avatars`
4. `report_orphans()` —— 报告孤儿数据（引用了已删除用户/帖子的记录）

输出示例：

```text
[13:46:30] === 初始化开始 ===
[13:46:35] ✓ 表结构检查完成
[13:46:47] ✓ 存储桶已存在: photos
[13:46:47] ✓ 存储桶已存在: contest_images
[13:46:47] ✓ 存储桶已存在: avatars
[13:46:56] === 初始化完成 ===
[13:46:56] 本次新增 0 列，新建 0 个存储桶
```

!!! danger "部署顺序铁律"
    **先跑 `init.py` 建表/补列，再推代码上线。**
    反过来做的话，新代码一上线就会因为查询不存在的表/列而 500。这是本项目最容易踩的部署事故。

### 为什么不自动迁移

Vercel 是 Serverless：每次冷启动都是新实例，把 DDL 塞进启动流程会拖慢冷启动、还会占用 Supabase 免费计划的连接配额。所以迁移是**显式的一次性运维动作**，不进启动流程。

`run.py` 里留了一个显式开关，本地/一次性运维可用，**线上不要长期开**：

```bash
# 只建缺失的表（等价于 create_all），不会补列
INIT_DB=1 python run.py
```

## 3. 表清单（15 张）

| 表 | 用途 | 关键字段 |
|---|---|---|
| `user` | 用户 | `username`、`password_hash`、`qq`、`email`、`avatar_url`、`is_staff`、`is_owner`、`preferences`(JSON 文本)、`registered_at` |
| `message` | 论坛帖子 | `user_id`、`title`、`content`、`created_at` |
| `reply` | 帖子回复 | `message_id`、`user_id`、`content` |
| `activity` | 社团活动 | `title`、`date`、`content` |
| `photo` | 照片墙 | Supabase Storage 里的图片 URL + 上传者 |
| `contests` | 赛事 | `title`、`type`、`gender_mode`、`status`、`open_at`、`close_at`、`config`(JSON) |
| `nominations` | 角色提名 | `contest_id`、`user_id`、`name`、`status` |
| `candidates` | 参战角色 | `contest_id`、`name`、`source`、`gender`、`image_url`、`stage`、`banned`、票数缓存 |
| `contest_votes` | 投票记录 | `contest_id`、`candidate_id`、`user_id`、`phase`、`weight` |
| `notifications` | 站内通知 | `user_id`、`type`(contest/audit/reply/system)、`link`、`is_read` |
| `ai_personas` | AI 好友角色人设 | `user_id`、`name`、`personality`、`speech_style`、`is_public`、`status` |
| `ai_chat_messages` | AI 好友聊天记录 | `user_id`、`persona_id`、`role`、`content`、`model`、`is_fallback` |
| `video_cache` | 视频解析缓存 | `cache_key`、`data`(JSON)、`expires_at` |
| `video_hosts` | 视频源白名单 | 域名、启用状态 |
| `video_proxy_logs` | 视频代理审计 | `url`、`method`、`status`、`ip_address` |
| `admin_logs` | 管理员操作留痕 | `user_id`、`username`、`action`、`target`、`detail`、`ip`、`created_at` |
| `rate_limits` | 跨实例限流计数 | `key`(主键)、`count`、`window_start` |

### 索引

热点查询都建了索引（`init.py` 的 `ensure_indexes()` 幂等补齐，`CREATE INDEX IF NOT EXISTS`，
PostgreSQL / SQLite 通用）：

| 表 | 索引 |
|---|---|
| `message` | `timestamp`、`user_id` |
| `reply` | `timestamp`、`message_id`、`user_id`、`parent_reply_id` |
| `notifications` | `user_id`、`created_at`、`(user_id, is_read)` 复合（未读红点查询） |
| `nominations` / `candidates` | `contest_id`、`user_id` |
| `contest_votes` | `contest_id`、`candidate_id`、`user_id` |
| `photo` | `activity_id` |
| `admin_logs` | `created_at` |

!!! note "为什么索引不在 create_all 里"
    `db.create_all()` **只给新建的表**建索引，已存在的表不会补。所以 `init.py` 单独用
    `CREATE INDEX IF NOT EXISTS` 补一遍，既兼容老库也能重复执行。

## 4. 备份与恢复

- **自动备份**：Supabase 免费计划提供每日备份（保留期有限）；要长期保留自己在 **Database → Backups** 确认。
- **手动导出**（推荐每次大改前做一次）：

```bash
# 只导数据，不导结构
pg_dump "$DATABASE_URL" --data-only --no-owner > backup-$(date +%F).sql

# 恢复
psql "$DATABASE_URL" < backup-2026-10-22.sql
```

- **Storage 桶**（头像/照片/赛事图）不在数据库备份里，需要单独用 Supabase 控制台或 API 导出。

## 5. 常见问题

| 现象 | 原因 | 处理 |
|---|---|---|
| `NoSuchTableError: users` | 表名是单数 `user` | 改查询；或确认 `init.py` 跑过 |
| 线上 500，日志 `column ... does not exist` | 代码先上线、`init.py` 没跑 | 先 `init.py`，再回滚/重部署代码 |
| 连接超时 / `too many clients` | 用了直连而非 pooler，或并发过高 | 换 `6543` pooler；检查连接池配置 |
| `SSL connection is required` | 连接串缺 `?sslmode=require` | 补参数（Supabase 默认要求 SSL） |
| 图片 404 | Storage 桶没建 / 桶权限是 private | 跑 `init.py` 的建桶步骤；检查桶策略 |
| `prepared statement ... already exists` | pooler + 旧版驱动兼容问题 | 换直连做迁移；或升级 SQLAlchemy/psycopg2 |
