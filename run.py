"""
南平一中动漫社官网 · 项目启动入口
====================================

本文件是整个应用的启动入口。
它通过导入 create_app() 工厂函数创建 Flask 应用实例，
并在 __main__ 中启动开发服务器。

使用方式：
    python run.py

生产环境建议使用 gunicorn 或其他 WSGI 服务器启动：
    gunicorn run:app

注意：
- 数据库表结构、存储桶、头像迁移等一次性运维操作，请跑 init.py
- 本文件只在 TESTING=1 时执行测试数据初始化
"""

import os
from datetime import datetime, timedelta

from app import create_app, db
from app.models import User, Contest, Candidate

app = create_app()


# ====== 测试环境自动创建测试数据 ======
if os.getenv('TESTING') == '1':
    with app.app_context():
        print("测试模式：正在初始化测试数据...")

        # 内存 SQLite 每次都是空的，需要建表
        db.create_all()

        # 1. 创建测试用户（带后台权限）
        user = User.query.filter_by(username='testuser').first()
        if user:
            user.set_password('password123')
        else:
            user = User(username='testuser', qq='123456789', email='test@qq.com')
            user.set_password('password123')
            user.is_staff = True
            user.is_owner = True
            db.session.add(user)
        db.session.commit()
        print("  - 测试用户已就绪: testuser")

        # 2. 创建测试赛事
        if not Contest.query.filter_by(title='测试赛事').first():
            contest = Contest(
                title='测试赛事',
                description='用于前端自动化测试的赛事',
                type='saimoe',
                gender_mode='separate',
                status='open',
                open_at=datetime.now() - timedelta(days=1),
                close_at=datetime.now() + timedelta(days=50),
                config={}
            )
            db.session.add(contest)
            db.session.commit()
            print(f"  - 创建测试赛事: ID={contest.id}")

            for i in range(5):
                db.session.add(Candidate(
                    contest_id=contest.id,
                    name=f'测试女角色{i}',
                    source='测试作品',
                    gender='female',
                    image_url='https://via.placeholder.com/80/ff6b6b?text=F' + str(i),
                    stage='pending'
                ))
            for i in range(5):
                db.session.add(Candidate(
                    contest_id=contest.id,
                    name=f'测试男角色{i}',
                    source='测试作品',
                    gender='male',
                    image_url='https://via.placeholder.com/80/4dabf7?text=M' + str(i),
                    stage='pending'
                ))
            db.session.commit()
            print("  - 创建候选角色: 女组5个，男组5个")

        db.session.commit()
        print("测试数据初始化完成")
# ====================================


# ====== 生产环境可选：显式开关式表结构同步（默认关闭）======
# app/models.py 新增的 video_hosts / video_cache / video_proxy_logs 三张表，
# 线上库不会自动出现（旧逻辑只在 TESTING=1 时建表），相关功能会直接 500。
# 这里提供一个"只在显式设置时才跑"的开关，默认绝不执行任何 DDL。
#
# 用法（本地/一次性运维，不要在 Vercel 上长期开启）：
#     INIT_DB=1 python run.py
#
# 局限：db.create_all() 只新建"缺失的表"，不会给已存在的 user 表补列。
#       补列请执行 docs/sql/v4.15.2_add_video_proxy_and_session.sql，
#       或运行既有的运维脚本 python init.py。
# 为什么不默认开启：Vercel Serverless 每次冷启动都是新实例，常驻 create_all
#       会拖慢冷启动并额外占用 Supabase 免费计划的连接配额。
if os.getenv('INIT_DB') == '1':
    with app.app_context():
        print("INIT_DB=1：正在按 app/models.py 同步数据库表结构（幂等，只新建缺失的表）...")
        db.create_all()
        print("已按 INIT_DB=1 同步表结构")
# ==========================================================


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)
