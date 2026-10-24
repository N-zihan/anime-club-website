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
- 测试数据初始化只在「TESTING=1 + 非 Vercel + SQLite 库」三条同时满足时执行，
  生产库（Postgres）一律拒绝，避免演示数据写进线上
"""

import os
from datetime import datetime, timedelta
from urllib.parse import quote

from app import create_app, db
from app.models import (User, Contest, Candidate, Activity, Photo,
                        Notification, AIPersona, AIChatMessage, AnimeListEntry)

app = create_app()


# ====== 测试环境自动创建测试数据 ======
def _demo_image(char: str, color: str) -> str:
    """生成「底色 + 首字」的 SVG 头像 data URI，供演示数据用（不依赖外网占位图）"""
    svg = (
        "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 80 80'>"
        f"<rect width='80' height='80' rx='18' fill='{color}'/>"
        "<text x='40' y='54' text-anchor='middle' font-size='38' font-family='sans-serif'"
        f" font-weight='600' fill='white'>{char}</text></svg>"
    )
    return 'data:image/svg+xml;charset=utf-8,' + quote(svg)


def _demo_seed_allowed() -> bool:
    """演示数据只在本地 / CI 的 SQLite 库执行；生产库（Postgres）一律拒绝。

    背景：run.py 里这套「测试赛事 / 演示成员 / 演示活动」本来是给本地截图和
    Playwright e2e 用的。曾经踩过的坑：本地 .env 的 DATABASE_URL 指向 Supabase
    生产库时，`TESTING=1 python run.py` 会把这些数据直接写进线上（测试赛事
    自己上线，还多出一个 password123 的 owner 账号）。这里把口子焊死。
    """
    from app.utils import demo_seed_allowed

    return demo_seed_allowed(
        app.config.get('SQLALCHEMY_DATABASE_URI', ''),
        os.getenv('TESTING') == '1',
        bool(os.getenv('VERCEL')),
    )


if _demo_seed_allowed():
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
                    image_url=_demo_image('女', ('#e46c9a', '#d95c8a', '#c74f80', '#e878a6', '#b8467a')[i]),
                    stage='pending'
                ))
            for i in range(5):
                db.session.add(Candidate(
                    contest_id=contest.id,
                    name=f'测试男角色{i}',
                    source='测试作品',
                    gender='male',
                    image_url=_demo_image('男', ('#5b9bea', '#4a86d6', '#3f7fd0', '#6c8ae4', '#4a68c9')[i]),
                    stage='pending'
                ))
            db.session.commit()
            print("  - 创建候选角色: 女组5个，男组5个")

        # 3. 演示成员：让社员名单 / 社团简介 / 论坛头像在本地与 CI 里不是只有一个账号（仅 TESTING）
        demo_members = [
            ('夏目', 320), ('星野', 260), ('千夏', 205),
            ('桐谷', 150), ('柚子', 96), ('阿澈', 40),
        ]
        for name, days_ago in demo_members:
            if not User.query.filter_by(username=name).first():
                member = User(username=name, qq=f'100{len(name)}{days_ago}',
                              email=f'{name}@example.com')
                member.set_password('password123')
                member.registered_at = datetime.now() - timedelta(days=days_ago)
                db.session.add(member)
        print(f"  - 演示成员已就绪: {len(demo_members)} 位")

        # 4. 演示活动：近期活动页与社团简介的统计需要真实记录（仅 TESTING）
        demo_activities = [
            ('十月新番放映会', '2026-10-18',
             '在社团活动室一起看了《药屋少女的呢喃》新一集，散场后聊了半小时剧情和原作差距。'),
            ('社团招新摆摊', '2026-09-25',
             '新学期招新，现场画 Q 版头像送新同学，一共收了 30 多份入社申请。'),
            ('夏日同人绘展', '2026-08-16',
             '社员投稿 40 多张插画，在走廊展出两周，最后投票选出三张人气作品。'),
            ('萌王大赛决赛夜', '2026-07-20',
             '男女组决赛同时进行，投影实时票数，咬到加时赛才分出胜负。'),
        ]
        for title, date, content in demo_activities:
            if not Activity.query.filter_by(title=title).first():
                db.session.add(Activity(title=title, date=date, content=content))
        print(f"  - 演示活动已就绪: {len(demo_activities)} 场")

        # 5. 演示照片墙：三张占位照片（文件名即可，截图时由测试脚本替换图片）
        if Photo.query.count() == 0:
            for idx, fname in enumerate(
                    ['demo-screening.jpg', 'demo-figure.jpg', 'demo-cosplay.jpg'], start=1):
                db.session.add(Photo(filename=fname, uploader='testuser'))
            print("  - 演示照片已就绪: 3 张")

        # 6. 演示通知：通知中心截图需要真实记录（仅 TESTING）
        if Notification.query.count() == 0:
            demo_notices = [
                ('你的帖子有新回复', '「十月新番大家追了哪几部？」收到了 1 条回复', 'reply'),
                ('提名审核通过', '你提名的角色已通过审核，等待海选开始', 'audit'),
                ('秋季萌战赛程已公布', '海选将于本周末开启，记得来投票', 'contest'),
                ('站点维护通知', '本周日凌晨将进行数据库例行维护，预计 10 分钟', 'system'),
            ]
            for title, content, ntype in demo_notices:
                db.session.add(Notification(user_id=user.id, title=title,
                                            content=content, type=ntype))
            print(f"  - 演示通知已就绪: {len(demo_notices)} 条")

        # 7. 演示 AI 人设：AI 好友页与管理后台截图需要至少一个角色（仅 TESTING）
        if AIPersona.query.count() == 0:
            db.session.add(AIPersona(
                user_id=user.id,
                name='中野三玖',
                source='五等分的新娘',
                personality='内向,认真,爱历史',
                speech_style='话少、说话轻声，第一人称「我」',
                catchphrases='……嗯',
                address_user='前辈',
                is_public=True,
            ))
            db.session.add(AIChatMessage(user_id=user.id, role='user',
                                         content='前辈好，最近想补一部治愈番，有推荐吗？'))
            db.session.add(AIChatMessage(user_id=user.id, role='assistant',
                                         content='《夏目友人帐》吧，温柔又治愈，适合慢慢看。',
                                         model='demo-free-model'))
            print("  - 演示 AI 人设已就绪: 1 个角色 + 2 条聊天")

        # 8. 演示追番清单：我的追番页/文档截图需要至少两条记录（仅 TESTING）
        if not AnimeListEntry.query.filter_by(user_id=user.id).first():
            db.session.add_all([
                AnimeListEntry(user_id=user.id, bangumi_id=400602, title='五等分的新娘',
                              status='done', rating=9),
                AnimeListEntry(user_id=user.id, bangumi_id=302766, title='CLANNAD',
                              status='watching'),
                AnimeListEntry(user_id=user.id, bangumi_id=218971, title='紫罗兰永恒花园',
                              status='want'),
            ])
            db.session.commit()
            print("  - 演示追番清单已就绪: 3 条")

        db.session.commit()
        print("测试数据初始化完成")
elif os.getenv('TESTING') == '1':
    print("检测到 TESTING=1，但当前数据库不是 SQLite，已拒绝写入演示数据（保护生产库）")
# ====================================


# ====== 生产环境可选：显式开关式表结构同步（默认关闭）======
# app/models.py 里的表（video_* 已于 v4.29.0 移除，表已在生产库 drop），
# 线上库不会自动出现（旧逻辑只在 TESTING=1 时建表），相关功能会直接 500。
# 这里提供一个"只在显式设置时才跑"的开关，默认绝不执行任何 DDL。
#
# 用法（本地/一次性运维，不要在 Vercel 上长期开启）：
#     INIT_DB=1 python run.py
#
# 局限：db.create_all() 只新建"缺失的表"，不会给已存在的 user 表补列。
#       补列请运行既有运维脚本 python init.py（该脚本幂等，只加列不删列）。
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
