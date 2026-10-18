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
from app.video_proxy import init_video_proxy

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


def main():
    log('=== 初始化开始 ===')

    try:
        db.create_all()
        log('✓ 表结构检查完成')
    except Exception as e:
        log(f'✗ 建表失败: {e}')
        sys.exit(1)

    added_cols = ensure_columns()
    created_buckets = ensure_buckets()
    
    # 初始化视频代理系统
    try:
        with app.app_context():
            init_video_proxy()
        log('✓ 视频代理系统初始化完成')
    except Exception as e:
        log(f'✗ 视频代理系统初始化失败: {e}')

    try:
        report_orphans()
    except Exception as e:
        log(f'orphan 检查跳过: {e}')

    log('=== 初始化完成 ===')
    log(f'本次新增 {added_cols} 列，新建 {created_buckets} 个存储桶')


if __name__ == '__main__':
    with app.app_context():
        main()
