"""
动漫社官网 · 数据库模型定义
======================================

本模块使用 SQLAlchemy ORM 定义了网站的所有数据表结构

所有模型均使用 UTC 时间存储，显示时转换为东八区。
"""

from datetime import datetime, timezone, timedelta

from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash

db = SQLAlchemy()


class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    qq = db.Column(db.String(20), unique=True, nullable=False)
    email = db.Column(db.String(100), unique=True, nullable=True)
    password_hash = db.Column(db.String(255), nullable=False)
    registered_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc) + timedelta(hours=8))
    avatar_url = db.Column(db.String(500), nullable=True)
    is_staff = db.Column(db.Boolean, default=False)
    is_owner = db.Column(db.Boolean, default=False)
    reset_token = db.Column(db.String(64), nullable=True)
    reset_token_expires = db.Column(db.DateTime, nullable=True)
    # 会话安全字段
    session_token = db.Column(db.String(255), nullable=True)
    session_token_expires = db.Column(db.DateTime, nullable=True)
    # 账号级偏好（JSON 文本，读写规则见 app/preferences.py）：NULL 表示全部走默认值
    preferences = db.Column(db.Text, nullable=True)
    
    def generate_session_token(self):
        """生成安全的会话令牌"""
        import secrets
        self.session_token = secrets.token_urlsafe(32)
        self.session_token_expires = datetime.now(timezone.utc) + timedelta(days=7)
    
    def validate_session_token(self, token):
        """验证会话令牌"""
        if not self.session_token or not self.session_token_expires:
            return False
        if self.session_token != token:
            return False
        return datetime.now(timezone.utc) < self.session_token_expires
        
    def validate_password_reset_token(self, token):
        """验证密码重置令牌安全性"""
        import secrets
        if not self.reset_token:
            return False
        # 令牌格式验证（UUID格式）
        try:
            secrets.token_urlsafe(32)
        except:
            return False
        # 检查令牌是否过期
        if not self.reset_token_expires or datetime.now(timezone.utc) > self.reset_token_expires:
            return False
        return self.reset_token == token

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)


class Message(db.Model):
    """论坛主题帖。

    历史数据里它是旧的「留言」：没有标题，正文即内容。
    现在作为论坛帖使用，title 允许为空，展示时用正文首行兜底（见 display_title）。
    """
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(120), nullable=True)
    nickname = db.Column(db.String(50), nullable=False, default='匿名')
    content = db.Column(db.Text, nullable=False)
    timestamp = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    user = db.relationship('User', backref='messages')

    @property
    def display_title(self):
        """标题缺失时用正文首行兜底，保证旧留言在论坛列表里也有可读标题。"""
        if self.title:
            return self.title
        text = ' '.join((self.content or '').split())
        if not text:
            return '（无标题）'
        return text[:40] + ('…' if len(text) > 40 else '')


class Activity(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(100), nullable=False)
    date = db.Column(db.String(20), nullable=False)
    content = db.Column(db.Text, nullable=False)


class Reply(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nickname = db.Column(db.String(50), nullable=False, default='匿名')
    content = db.Column(db.Text, nullable=False)
    timestamp = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    message_id = db.Column(db.Integer, db.ForeignKey('message.id'), nullable=False)
    message = db.relationship('Message', backref=db.backref('replies', lazy='dynamic', order_by='Reply.timestamp'))
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    user = db.relationship('User', backref='replies')

    parent_reply_id = db.Column(db.Integer, db.ForeignKey('reply.id'), nullable=True)
    parent_reply = db.relationship('Reply', remote_side=[id], backref=db.backref('children', lazy='dynamic'))


class Photo(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    filename = db.Column(db.String(200), nullable=False)
    activity_id = db.Column(db.Integer, db.ForeignKey('activity.id'), nullable=True)
    activity = db.relationship('Activity', backref='photos')
    upload_time = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    uploader = db.Column(db.String(50))


# ========== 萌战系统 ==========

class Contest(db.Model):
    __tablename__ = 'contests'
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text, nullable=False)
    type = db.Column(db.String(20), nullable=False, default='saimoe')
    gender_mode = db.Column(db.String(10), nullable=False, default='separate')
    status = db.Column(db.String(20), nullable=False, default='draft')
    created_by = db.Column(db.Integer, db.ForeignKey('user.id'))
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    open_at = db.Column(db.DateTime, nullable=True)
    close_at = db.Column(db.DateTime, nullable=True)
    config = db.Column(db.JSON, nullable=True)

    creator = db.relationship('User', backref='created_contests')
    nominations = db.relationship('Nomination', back_populates='contest', lazy='dynamic', cascade='all, delete-orphan')
    candidates = db.relationship('Candidate', back_populates='contest', lazy='dynamic', cascade='all, delete-orphan')


class Nomination(db.Model):
    __tablename__ = 'nominations'
    id = db.Column(db.Integer, primary_key=True)
    contest_id = db.Column(db.Integer, db.ForeignKey('contests.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    name = db.Column(db.String(100), nullable=False)
    source = db.Column(db.String(200), nullable=False)
    gender = db.Column(db.String(10), nullable=False)
    image_url = db.Column(db.String(500), nullable=True)
    description = db.Column(db.Text, nullable=True)
    status = db.Column(db.String(20), default='pending')
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    user = db.relationship('User', backref='nominations')
    contest = db.relationship('Contest', back_populates='nominations')

    __table_args__ = (
        db.UniqueConstraint('contest_id', 'name', name='uq_nomination_contest_role'),
    )


class Candidate(db.Model):
    __tablename__ = 'candidates'
    id = db.Column(db.Integer, primary_key=True)
    contest_id = db.Column(db.Integer, db.ForeignKey('contests.id'), nullable=False)
    nomination_id = db.Column(db.Integer, db.ForeignKey('nominations.id'), nullable=True)
    name = db.Column(db.String(100), nullable=False)
    source = db.Column(db.String(200), nullable=False)
    gender = db.Column(db.String(10), nullable=False)
    image_url = db.Column(db.String(500), nullable=True)
    description = db.Column(db.Text, nullable=True)
    stage = db.Column(db.String(20), default='pending')
    banned = db.Column(db.Boolean, default=False, nullable=False)  # 禁赛标记：禁赛期间禁止投票
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    contest = db.relationship('Contest', back_populates='candidates')
    nomination = db.relationship('Nomination', backref='candidate')


class ContestVote(db.Model):
    __tablename__ = 'contest_votes'
    id = db.Column(db.Integer, primary_key=True)
    contest_id = db.Column(db.Integer, db.ForeignKey('contests.id'), nullable=False)
    candidate_id = db.Column(db.Integer, db.ForeignKey('candidates.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    weight = db.Column(db.Integer, default=1)
    round_number = db.Column(db.Integer, default=0)
    gender = db.Column(db.String(10), nullable=True)
    sub_round = db.Column(db.Integer, default=0)
    match_index = db.Column(db.Integer, default=0)
    group_index = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        db.UniqueConstraint('contest_id', 'round_number', 'user_id', 'gender', 'candidate_id',
                            'sub_round', 'match_index', 'group_index',
                            name='uq_contest_vote_unique'),
    )


class Notification(db.Model):
    __tablename__ = 'notifications'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    title = db.Column(db.String(200), nullable=False)
    content = db.Column(db.Text, nullable=True)
    type = db.Column(db.String(20), nullable=False)  # contest / audit / reply / system
    link = db.Column(db.String(500), nullable=True)
    is_read = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))


# ========== 视频代理系统 ==========

class VideoHost(db.Model):
    __tablename__ = 'video_hosts'
    id = db.Column(db.Integer, primary_key=True)
    host = db.Column(db.String(200), nullable=False, unique=True)
    is_trusted = db.Column(db.Boolean, default=False)
    description = db.Column(db.Text, nullable=True)
    added_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    request_count = db.Column(db.Integer, default=0)
    success_count = db.Column(db.Integer, default=0)


class VideoCache(db.Model):
    __tablename__ = 'video_cache'
    id = db.Column(db.Integer, primary_key=True)
    cache_key = db.Column(db.String(64), nullable=False, unique=True)
    data = db.Column(db.JSON, nullable=False)
    expires_at = db.Column(db.DateTime, nullable=False)
    platform = db.Column(db.String(50), nullable=True)
    video_id = db.Column(db.String(100), nullable=True)
    access_count = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))


class VideoProxy(db.Model):
    __tablename__ = 'video_proxy_logs'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    url = db.Column(db.String(500), nullable=False)
    method = db.Column(db.String(10), nullable=False)
    status = db.Column(db.String(20), nullable=False)  # pending/processing/completed/failed
    error_message = db.Column(db.Text, nullable=True)
    proxy_time = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    ip_address = db.Column(db.String(50), nullable=True)
    user_agent = db.Column(db.Text, nullable=True)
    content_type = db.Column(db.String(100), nullable=True)
    size = db.Column(db.Integer, nullable=True)

    user = db.relationship('User', backref='video_proxy_logs')


# ========== AI 虚拟好友 ==========
class AIPersona(db.Model):
    """用户自定义的 AI 角色人格。

    人设内容全部由用户手填（或免费模型辅助补全），不抓取外部网页；
    is_public=False 表示仅创建者可见；status='hidden' 表示被管理员下架。
    """
    __tablename__ = 'ai_personas'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, index=True)
    name = db.Column(db.String(30), nullable=False)
    source = db.Column(db.String(50), nullable=True)
    personality = db.Column(db.String(200), nullable=False)   # 逗号分隔的性格标签
    speech_style = db.Column(db.Text, nullable=False)         # 说话风格
    catchphrases = db.Column(db.String(200), nullable=True)   # 逗号分隔，最多 3 个
    address_user = db.Column(db.String(30), nullable=True)    # 对用户的称呼
    is_public = db.Column(db.Boolean, default=False, nullable=False)
    status = db.Column(db.String(20), default='active', nullable=False)  # active / hidden
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    user = db.relationship('User', backref=db.backref('ai_personas', lazy='dynamic'))

    @property
    def visibility_label(self):
        return '公开' if self.is_public else '仅自己'


class AIChatMessage(db.Model):
    """AI 好友聊天记录：只用于上下文与用量限额，不对外展示。"""
    __tablename__ = 'ai_chat_messages'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, index=True)
    persona_id = db.Column(db.Integer, db.ForeignKey('ai_personas.id'), nullable=True)
    role = db.Column(db.String(10), nullable=False)   # user / assistant
    content = db.Column(db.Text, nullable=False)
    model = db.Column(db.String(60), nullable=True)   # 实际回答的模型（失败兜底为 null）
    is_fallback = db.Column(db.Boolean, default=False, nullable=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), index=True)

    persona = db.relationship('AIPersona')
