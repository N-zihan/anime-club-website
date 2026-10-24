"""
一次性运维脚本：建表、补列、建桶。

用法：
    python init.py

特点：
- 幂等：重复跑无害，只对缺失的部分做操作
- 只加列，不删列、不改类型（那些需要手动处理）
- 会提示数据库里存在但 models.py 里已删的列/表，附上清理 SQL
"""
import sys
from datetime import datetime

from sqlalchemy import inspect, text

from app import create_app, db
from app.utils import get_supabase

# Windows 控制台默认 GBK：log() 里的 ✓/✗ 会让 print 抛 UnicodeEncodeError，
# 异常又被上层 except 捕到，于是真正的操作结果被一行 traceback 吞掉（曾据此误判建表失败）。
# 统一把标准输出改成 UTF-8 + replace，保证日志永远打得出来。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding='utf-8', errors='replace')
    except (AttributeError, ValueError, OSError):
        pass

app = create_app()

REQUIRED_BUCKETS = ['photos', 'contest_images', 'avatars']


def log(msg):
    print(f'[{datetime.now().strftime("%H:%M:%S")}] {msg}')


def ensure_columns():
    """对比 models.py 和数据库，自动补缺失的列。返回新增列数。"""
    inspector = inspect(db.engine)
    dialect = db.engine.dialect
    existing_tables = set(inspector.get_table_names())
    added = 0

    for table_name, table in db.metadata.tables.items():
        if table_name not in existing_tables:
            continue

        existing_cols = {c['name'] for c in inspector.get_columns(table_name)}

        for column in table.columns:
            if column.name in existing_cols or column.primary_key:
                continue

            col_type = column.type.compile(dialect=dialect)
            ddl = f'ALTER TABLE "{table_name}" ADD COLUMN "{column.name}" {col_type}'

            # 非空列必须有默认值，否则已有行会报错
            if not column.nullable:
                default = getattr(column, 'default', None)
                arg = getattr(default, 'arg', None) if default is not None else None
                if arg is None or callable(arg):
                    log(f'✗ 跳过 {table_name}.{column.name}：非空但无静态默认值')
                    continue
                if isinstance(arg, str):
                    ddl += f" DEFAULT '{arg}'"
                elif isinstance(arg, bool):
                    ddl += f' DEFAULT {str(arg).upper()}'
                elif isinstance(arg, (int, float)):
                    ddl += f' DEFAULT {arg}'

            try:
                db.session.execute(text(ddl))
                log(f'✓ 已添加列: {table_name}.{column.name} ({col_type})')
                added += 1
            except Exception as e:
                log(f'✗ 添加列失败 {table_name}.{column.name}: {e}')
                db.session.rollback()

    db.session.commit()
    return added


def report_orphans():
    """报告数据库里有、但 models.py 里已删的表和列（只提示，不自动处理）"""
    inspector = inspect(db.engine)
    existing_tables = set(inspector.get_table_names())
    model_tables = set(db.metadata.tables.keys())

    for t in sorted(existing_tables - model_tables):
        log(f'⚠️  多余表: {t}  →  DROP TABLE "{t}";')

    for table_name, table in db.metadata.tables.items():
        if table_name not in existing_tables:
            continue
        existing_cols = {c['name'] for c in inspector.get_columns(table_name)}
        model_cols = {c.name for c in table.columns}
        for c in sorted(existing_cols - model_cols):
            log(f'⚠️  多余列: {table_name}.{c}  →  ALTER TABLE "{table_name}" DROP COLUMN "{c}";')


def ensure_buckets():
    """检查并创建存储桶。返回新建数量。"""
    supabase = get_supabase()
    existing = supabase.storage.list_buckets()
    names = []
    for b in existing:
        if isinstance(b, dict):
            names.append(b.get('name'))
        else:
            names.append(getattr(b, 'name', None))

    created = 0
    for bucket in REQUIRED_BUCKETS:
        if bucket in names:
            log(f'✓ 存储桶已存在: {bucket}')
            continue
        try:
            supabase.storage.create_bucket(bucket, options={'public': True})
            log(f'✓ 已创建存储桶: {bucket} (public)')
            created += 1
        except TypeError:
            # 老版本 SDK 用 public 参数
            try:
                supabase.storage.create_bucket(bucket, public=True)
                log(f'✓ 已创建存储桶: {bucket} (public)')
                created += 1
            except Exception as e:
                log(f'✗ 创建存储桶失败 {bucket}: {e}')
        except Exception as e:
            log(f'✗ 创建存储桶失败 {bucket}: {e}')
    return created


# 热点查询索引：create_all() 只会给「新建的表」建索引，已有表不会补，
# 所以这里用 CREATE INDEX IF NOT EXISTS 幂等补一遍（兼容 PostgreSQL / SQLite）。
REQUIRED_INDEXES = [
    ('ix_message_timestamp', 'message', ['timestamp']),
    ('ix_message_user_id', 'message', ['user_id']),
    ('ix_reply_timestamp', 'reply', ['timestamp']),
    ('ix_reply_message_id', 'reply', ['message_id']),
    ('ix_reply_user_id', 'reply', ['user_id']),
    ('ix_reply_parent_reply_id', 'reply', ['parent_reply_id']),
    ('ix_photo_activity_id', 'photo', ['activity_id']),
    ('ix_notifications_user_id', 'notifications', ['user_id']),
    ('ix_notifications_created_at', 'notifications', ['created_at']),
    ('ix_notifications_user_read', 'notifications', ['user_id', 'is_read']),
    ('ix_nominations_contest_id', 'nominations', ['contest_id']),
    ('ix_nominations_user_id', 'nominations', ['user_id']),
    ('ix_candidates_contest_id', 'candidates', ['contest_id']),
    ('ix_contest_votes_contest_id', 'contest_votes', ['contest_id']),
    ('ix_contest_votes_candidate_id', 'contest_votes', ['candidate_id']),
    ('ix_contest_votes_user_id', 'contest_votes', ['user_id']),
    ('ix_admin_logs_created_at', 'admin_logs', ['created_at']),
    ('ix_anime_list_user_id', 'anime_list', ['user_id']),
    ('ix_anime_list_bangumi_id', 'anime_list', ['bangumi_id']),
]


def ensure_indexes():
    """幂等补齐热点索引，返回本次新建的数量。"""
    inspector = inspect(db.engine)
    tables = set(inspector.get_table_names())
    created = 0
    skipped_tables = set()

    for name, table, columns in REQUIRED_INDEXES:
        if table not in tables:
            skipped_tables.add(table)
            continue
        existing = {idx['name'] for idx in inspector.get_indexes(table)}
        if name in existing:
            continue
        cols = ', '.join(f'"{c}"' for c in columns)
        ddl = f'CREATE INDEX IF NOT EXISTS "{name}" ON "{table}" ({cols})'
        try:
            db.session.execute(text(ddl))
            db.session.commit()
            created += 1
            log(f'  + 索引 {name} ({table})')
        except Exception as exc:
            db.session.rollback()
            log(f'✗ 建索引失败 {name}: {exc}')

    if skipped_tables:
        log(f'  跳过（表不存在）: {", ".join(sorted(skipped_tables))}')
    return created



def main():
    log('=== 初始化开始 ===')

    try:
        db.create_all()
        log('✓ 表结构检查完成')
    except Exception as e:
        log(f'✗ 建表失败: {e}')
        sys.exit(1)

    added_cols = ensure_columns()
    added_indexes = ensure_indexes()
    created_buckets = ensure_buckets()
    
    try:
        report_orphans()
    except Exception as e:
        log(f'orphan 检查跳过: {e}')

    log('=== 初始化完成 ===')
    log(f'本次新增 {added_cols} 列 / {added_indexes} 个索引，新建 {created_buckets} 个存储桶')


if __name__ == '__main__':
    with app.app_context():
        main()
