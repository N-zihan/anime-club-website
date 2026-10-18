-- ============================================================================
-- 南平一中动漫社官网 · 数据库迁移脚本
-- 版本：v4.15.2
-- 文件：docs/sql/v4.15.2_add_video_proxy_and_session.sql
-- 目标数据库：Supabase（PostgreSQL）
-- ============================================================================
--
-- 【用途】
--   同步 app/models.py 中新增、但线上库可能尚未存在的表结构与列：
--     1. 表 video_hosts         —— 视频代理主机白名单
--     2. 表 video_cache         —— 视频解析结果缓存
--     3. 表 video_proxy_logs    —— 视频代理请求日志
--     4. user.session_token          —— 会话安全令牌（新增列）
--     5. user.session_token_expires  —— 会话安全令牌过期时间（新增列）
--
--   为什么需要这个文件：
--   run.py 里的 db.create_all() 原先只在 TESTING=1 分支内执行，生产启动时
--   既不建表也不 ALTER 列。若线上库缺少上述表/列，任何触及它们的代码路径
--   （app/video_proxy.py 的白名单/缓存/统计接口）都会直接 500。
--
-- 【⚠️ 执行前请务必先备份】
--   本脚本是生产库的 DDL 变更。执行前请先在 Supabase 后台
--   Database → Backups 创建一次备份（或至少导出相关表），确认备份成功后再执行。
--   本脚本不会 DROP / TRUNCATE / DELETE / UPDATE 任何数据，只做"缺失才创建"。
--
-- 【如何在 Supabase 后台执行】
--   1. 登录 https://supabase.com/dashboard ，进入本项目；
--   2. 左侧菜单点击 "SQL Editor" → 右上角 "New query"；
--   3. 把本文件【全文】复制粘贴进去；
--   4. 点击右下角 "Run"（或按 Ctrl+Enter）；
--   5. 预期结果：Success. No rows returned（成功，无返回行）；
--   6. 验证：左侧 "Table Editor" 应能看到 video_hosts / video_cache /
--      video_proxy_logs 三张表；user 表应多出 session_token /
--      session_token_expires 两列。
--
-- 【幂等性】
--   本脚本可以安全地重复执行任意次：
--     · 建表用 CREATE TABLE IF NOT EXISTS
--     · 补列用 ALTER TABLE ... ADD COLUMN IF NOT EXISTS
--     · 唯一约束用 DO 块先查 pg_index 再决定是否创建
--     · 索引用 CREATE INDEX IF NOT EXISTS
--   重复执行不会报错，也不会改变已有数据。
--
-- 【与 SQLAlchemy 模型的一致性】
--   字段名、类型、可空性严格对齐 app/models.py 中的 VideoHost / VideoCache /
--   VideoProxy 模型与 User 模型。唯一一处便利性差异见下方「关于默认值」。
--
-- 【关于默认值（重要，请勿误解）】
--   模型里 added_at / created_at / proxy_time / request_count / success_count /
--   access_count / is_trusted 的默认值都是 **Python 侧默认**
--   （db.Column(default=...)），由应用写入时提供，数据库层面本来没有 DEFAULT。
--   本脚本为方便手工插入/排查，给这些列补了等价的 SQL 层 DEFAULT
--   （时间列 now() AT TIME ZONE 'UTC'，计数列 0，布尔列 false）。
--   这只会让行为更宽松，不会与 ORM 冲突，也不影响已有数据。
--
-- ============================================================================


BEGIN;

-- ----------------------------------------------------------------------------
-- 0. user 表补列：会话安全令牌
--    注意：user 是 PostgreSQL 的保留字，**必须用双引号包裹**。
--    这两列在模型中均为 nullable=True，无唯一约束、无索引。
--    当前代码库中暂无引用点（generate_session_token / validate_session_token
--    已定义但尚未被任何路由调用），但为保持库结构与应用模型一致，一并补齐。
-- ----------------------------------------------------------------------------
ALTER TABLE "user" ADD COLUMN IF NOT EXISTS session_token VARCHAR(255);
ALTER TABLE "user" ADD COLUMN IF NOT EXISTS session_token_expires TIMESTAMP WITHOUT TIME ZONE;


-- ----------------------------------------------------------------------------
-- 1. video_hosts —— 视频代理主机白名单
--    对应 app/models.py 的 class VideoHost
--    host 是 VARCHAR(200) NOT NULL UNIQUE
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS video_hosts (
    id              SERIAL PRIMARY KEY,
    host            VARCHAR(200) NOT NULL,
    is_trusted      BOOLEAN DEFAULT false,
    description     TEXT,
    added_at        TIMESTAMP WITHOUT TIME ZONE DEFAULT (now() AT TIME ZONE 'UTC'),
    request_count   INTEGER DEFAULT 0,
    success_count   INTEGER DEFAULT 0
);

-- host 的唯一约束：先查 pg_index，只有确实不存在唯一索引时才创建，保证幂等。
-- （这样即使该表是别人手工建的、或约束名不同，也不会重复创建报错。）
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_index i
        JOIN pg_class c ON c.oid = i.indrelid
        JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum = ANY (i.indkey)
        WHERE c.relname = 'video_hosts'
          AND c.relnamespace = 'public'::regnamespace
          AND a.attname = 'host'
          AND i.indisunique
    ) THEN
        ALTER TABLE video_hosts ADD CONSTRAINT uq_video_hosts_host UNIQUE (host);
    END IF;
END $$;


-- ----------------------------------------------------------------------------
-- 2. video_cache —— 视频解析结果缓存
--    对应 app/models.py 的 class VideoCache
--    data 是 JSON NOT NULL（PostgreSQL 用 JSON 类型，不是 JSONB，与模型一致）
--    cache_key 是 VARCHAR(64) NOT NULL UNIQUE
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS video_cache (
    id              SERIAL PRIMARY KEY,
    cache_key       VARCHAR(64) NOT NULL,
    data            JSON NOT NULL,
    expires_at      TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    platform        VARCHAR(50),
    video_id        VARCHAR(100),
    access_count    INTEGER DEFAULT 0,
    created_at      TIMESTAMP WITHOUT TIME ZONE DEFAULT (now() AT TIME ZONE 'UTC')
);

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_index i
        JOIN pg_class c ON c.oid = i.indrelid
        JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum = ANY (i.indkey)
        WHERE c.relname = 'video_cache'
          AND c.relnamespace = 'public'::regnamespace
          AND a.attname = 'cache_key'
          AND i.indisunique
    ) THEN
        ALTER TABLE video_cache ADD CONSTRAINT uq_video_cache_cache_key UNIQUE (cache_key);
    END IF;
END $$;

-- expires_at 是缓存清理（删除过期记录）的过滤列，加索引避免全表扫描。
CREATE INDEX IF NOT EXISTS ix_video_cache_expires_at ON video_cache (expires_at);


-- ----------------------------------------------------------------------------
-- 3. video_proxy_logs —— 视频代理请求日志
--    对应 app/models.py 的 class VideoProxy（注意类名与表名不一致）
--    user_id 可空，外键指向 "user"(id)
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS video_proxy_logs (
    id              SERIAL PRIMARY KEY,
    user_id         INTEGER,
    url             VARCHAR(500) NOT NULL,
    method          VARCHAR(10) NOT NULL,
    status          VARCHAR(20) NOT NULL,
    error_message   TEXT,
    proxy_time      TIMESTAMP WITHOUT TIME ZONE DEFAULT (now() AT TIME ZONE 'UTC'),
    ip_address      VARCHAR(50),
    user_agent      TEXT,
    content_type    VARCHAR(100),
    size            INTEGER
);

-- user_id 外键指向 "user"(id)：若表已存在但缺外键，则补上；已存在则跳过（幂等）。
-- 注意：这里刻意不用建表语句里的内联 REFERENCES —— 内联外键会被 PostgreSQL 自动
-- 命名为 video_proxy_logs_user_id_fkey，与下面的固定约束名不同，会导致重复创建
-- 第二个外键，且因约束名不在 pg_constraint 查询结果里，DO 块的幂等判断会失效。
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conrelid = 'video_proxy_logs'::regclass
          AND contype = 'f'
          AND conname = 'fk_video_proxy_logs_user_id'
    ) THEN
        ALTER TABLE video_proxy_logs
            ADD CONSTRAINT fk_video_proxy_logs_user_id
            FOREIGN KEY (user_id) REFERENCES "user" (id);
    END IF;
END $$;

-- 说明：与 app/models.py 严格对齐，这里不为 user_id 建索引 ——
-- 模型里没有 index=True，且当前代码库（app/video_proxy.py）从未按 user_id 查询。
-- 若后续新增"按用户查代理日志"的功能，再补：
--     CREATE INDEX IF NOT EXISTS ix_video_proxy_logs_user_id ON video_proxy_logs (user_id);



COMMIT;


-- ============================================================================
-- 执行后自检（可选，把下面几行单独选中运行即可验证结果）
-- ============================================================================
-- SELECT table_name FROM information_schema.tables
--   WHERE table_schema = 'public'
--     AND table_name IN ('video_hosts', 'video_cache', 'video_proxy_logs');
--
-- SELECT column_name, data_type, is_nullable
--   FROM information_schema.columns
--   WHERE table_schema = 'public' AND table_name = 'user'
--     AND column_name IN ('session_token', 'session_token_expires');
--
-- ============================================================================
-- 回滚说明（仅在确需撤销时手工执行；本脚本不做任何删除）
-- ============================================================================
-- 如需完全回滚，请确认这三张表确实没有业务数据后，手工执行：
--   DROP TABLE IF EXISTS video_proxy_logs;
--   DROP TABLE IF EXISTS video_cache;
--   DROP TABLE IF EXISTS video_hosts;
--   ALTER TABLE "user" DROP COLUMN IF EXISTS session_token;
--   ALTER TABLE "user" DROP COLUMN IF EXISTS session_token_expires;
-- （注意：DROP COLUMN 会永久丢失该列数据，执行前务必备份。）
-- ============================================================================
