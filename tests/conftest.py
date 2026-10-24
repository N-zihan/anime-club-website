import os
import pytest
import sys
from datetime import datetime, timedelta

# ====== 设置测试环境变量（必须在导入 app 之前） ======
os.environ['GROUP_VERIFICATION_CODE'] = 'test_group_code'
os.environ['TESTING'] = '1'
os.environ['SECRET_KEY'] = 'test-secret-key-for-ci'
os.environ['SUPABASE_URL'] = 'https://fake.supabase.co'
os.environ['SUPABASE_KEY'] = 'fake-key'
# 单元测试没有请求上下文：给新闻上游一个固定假地址，避免真实外网
os.environ.setdefault('ANI_NEWS_API', 'https://news.test.local')
# =====================================================

# 获取项目根目录
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

# 强制使用 SQLite 内存数据库
os.environ['DATABASE_URL'] = 'sqlite:///:memory:'

# 现在可以正常导入 app 模块
from app import create_app
from app.models import db, User, Contest, Candidate, Nomination, ContestVote, Activity, Message


@pytest.fixture
def app():
    """创建测试用 Flask 应用"""
    app = create_app()
    app.config['TESTING'] = True
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///:memory:'
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    app.config['WTF_CSRF_ENABLED'] = False
    yield app
    # 释放连接，避免 Python 3.14 在 GC 时刷大量 unclosed database 警告
    with app.app_context():
        db.session.remove()
        db.engine.dispose()


@pytest.fixture
def client(app):
    """未登录测试客户端"""
    return app.test_client()


@pytest.fixture
def logged_in_client(app, sample_user):
    """已登录测试客户端"""
    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess['user_id'] = sample_user.id
            sess['username'] = sample_user.username
            sess['user_role'] = 'member'
        yield client


@pytest.fixture
def admin_client(app, sample_admin):
    """管理员测试客户端"""
    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess['user_id'] = sample_admin.id
            sess['username'] = sample_admin.username
            sess['user_role'] = 'owner'
        yield client


@pytest.fixture
def db_session(app):
    """创建测试数据库会话"""
    with app.app_context():
        db.create_all()
        yield db.session
        db.session.remove()
        db.engine.dispose()
        db.drop_all()


@pytest.fixture
def sample_user(db_session):
    """创建示例普通用户"""
    user = User(username="testuser", qq="123456789")
    user.set_password("password123")
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


@pytest.fixture
def sample_admin(db_session):
    """创建示例管理员"""
    user = User(username="admin", qq="987654321")
    user.set_password("admin123")
    user.is_owner = True
    user.is_staff = True
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


@pytest.fixture
def sample_contest(db_session):
    """创建示例赛事（含32女+32男候选角色）"""
    open_at = datetime(2026, 10, 1, 18, 0, 0)
    contest = Contest(
        title="测试萌战",
        description="测试赛事描述",
        type='saimoe',
        gender_mode='separate',
        status='open',
        open_at=open_at,
        close_at=open_at + timedelta(days=50),
        config={}
    )
    db_session.add(contest)
    db_session.commit()

    # 32 女角色
    for i in range(32):
        c = Candidate(
            contest_id=contest.id,
            name=f"女角色{i}",
            source=f"作品{i}",
            gender='female',
            image_url=f"http://test.com/f{i}.jpg",
            stage='pending'
        )
        db_session.add(c)
    # 32 男角色
    for i in range(32):
        c = Candidate(
            contest_id=contest.id,
            name=f"男角色{i}",
            source=f"作品{i}",
            gender='male',
            image_url=f"http://test.com/m{i}.jpg",
            stage='pending'
        )
        db_session.add(c)
    db_session.commit()
    db_session.refresh(contest)
    return contest


@pytest.fixture
def sample_activity(db_session):
    """创建示例活动"""
    activity = Activity(
        title="测试活动",
        date="2026-10-15",
        content="这是测试活动的内容"
    )
    db_session.add(activity)
    db_session.commit()
    db_session.refresh(activity)
    return activity


@pytest.fixture
def sample_message(db_session, sample_user):
    """创建示例留言"""
    msg = Message(
        nickname=sample_user.username,
        content="测试留言内容",
        user_id=sample_user.id
    )
    db_session.add(msg)
    db_session.commit()
    db_session.refresh(msg)
    return msg


@pytest.fixture
def sample_nomination(db_session, sample_user, sample_contest):
    """创建示例提名"""
    nom = Nomination(
        contest_id=sample_contest.id,
        user_id=sample_user.id,
        name="测试角色",
        source="测试作品",
        gender='female',
        image_url="http://test.com/nom.jpg",
        description="测试提名",
        status='pending'
    )
    db_session.add(nom)
    db_session.commit()
    db_session.refresh(nom)
    return nom


@pytest.fixture
def sample_candidate(db_session, sample_contest):
    """创建示例候选角色"""
    cand = Candidate(
        contest_id=sample_contest.id,
        name="测试候选",
        source="测试作品",
        gender='female',
        image_url="http://test.com/cand.jpg",
        stage='pending'
    )
    db_session.add(cand)
    db_session.commit()
    db_session.refresh(cand)
    return cand


@pytest.fixture
def sample_vote(db_session, sample_contest, sample_user, sample_candidate):
    """创建示例投票"""
    vote = ContestVote(
        contest_id=sample_contest.id,
        candidate_id=sample_candidate.id,
        user_id=sample_user.id,
        weight=3,
        round_number=0,
        gender='female',
        sub_round=0,
        match_index=0
    )
    db_session.add(vote)
    db_session.commit()
    db_session.refresh(vote)
    return vote
