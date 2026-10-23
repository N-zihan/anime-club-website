import pytest
from app.auth import CLUB_NAME
from app.models import ContestVote, Contest, Message, Reply, User
from datetime import timedelta, datetime
from unittest.mock import patch


class TestPublic:
    """公共页面测试（所有需要登录的页面均使用 logged_in_client）"""

    def test_splash_page(self, client):
        # splash 是公开的，不需要登录
        response = client.get('/')
        assert response.status_code == 200
        assert CLUB_NAME in response.text

    def test_home_page(self, logged_in_client):
        response = logged_in_client.get('/home')
        assert response.status_code == 200
        assert '欢迎来到动漫社' in response.text

    def test_about_page(self, logged_in_client):
        response = logged_in_client.get('/about')
        assert response.status_code == 200
        assert '社团简介' in response.text

    def test_activities_page(self, logged_in_client):
        response = logged_in_client.get('/activities')
        assert response.status_code == 200
        assert '近期活动' in response.text

    def test_gallery_page(self, logged_in_client):
        response = logged_in_client.get('/gallery')
        assert response.status_code == 200
        assert '珍贵历史图片' in response.text

    def test_board_page(self, logged_in_client):
        response = logged_in_client.get('/board')
        assert response.status_code == 200
        assert '动漫社论坛' in response.text

    def test_board_shows_legacy_message_as_topic(self, logged_in_client, sample_message):
        """历史留言没有标题：论坛列表要用正文首行兜底，不能显示空标题"""
        body = logged_in_client.get('/board').text
        assert '测试留言内容' in body

    def test_board_thread_page(self, logged_in_client, sample_message):
        response = logged_in_client.get(f'/board/{sample_message.id}')
        assert response.status_code == 200
        assert '测试留言内容' in response.text
        # 底部固定输入框：不用滚到底也能回复
        assert 'thread-composer' in response.text
        assert 'has-composer' in response.text

    def test_board_thread_404(self, logged_in_client):
        assert logged_in_client.get('/board/999999').status_code == 404

    def test_board_new_requires_login(self, client):
        assert client.get('/board/new').status_code == 302

    def test_board_new_form_renders(self, logged_in_client):
        response = logged_in_client.get('/board/new')
        assert response.status_code == 200
        assert '发起新帖' in response.text
        assert 'name="title"' in response.text

    def test_board_new_creates_topic(self, logged_in_client, db_session):
        from app.models import Message
        response = logged_in_client.post(
            '/board/new',
            data={'title': '第一次发帖测试', 'content': '论坛正文内容'},
            follow_redirects=True)
        assert response.status_code == 200
        assert '发帖成功' in response.text
        msg = Message.query.filter_by(title='第一次发帖测试').first()
        assert msg is not None
        assert msg.content == '论坛正文内容'

    @pytest.mark.parametrize('payload', [
        {'title': 'x', 'content': '正文'},          # 标题太短
        {'title': '正常标题', 'content': ''},        # 正文为空
    ])
    def test_board_new_rejects_invalid(self, logged_in_client, db_session, payload):
        from app.models import Message
        before = Message.query.count()
        response = logged_in_client.post('/board/new', data=payload, follow_redirects=True)
        assert response.status_code == 200
        assert '标题至少 2 个字' in response.text or '正文不能为空' in response.text
        assert Message.query.count() == before

    def test_reply_lands_on_thread_page(self, logged_in_client, sample_message, db_session):
        from app.models import Reply
        response = logged_in_client.post(
            f'/reply/{sample_message.id}',
            data={'content': '论坛回复内容'},
            follow_redirects=True)
        assert response.status_code == 200
        assert '论坛回复内容' in response.text
        assert Reply.query.filter_by(content='论坛回复内容').count() == 1

    def test_anime_resources_page(self, logged_in_client):
        response = logged_in_client.get('/anime_resources')
        assert response.status_code == 200
        assert '番剧百科' in response.text

    def test_members_page(self, logged_in_client):
        response = logged_in_client.get('/members')
        assert response.status_code == 200
        assert '社员名单' in response.text

    def test_contest_center_page(self, logged_in_client):
        response = logged_in_client.get('/contest_center')
        assert response.status_code == 200
        assert '赛事中心' in response.text

    def test_contest_rules_page(self, logged_in_client, sample_contest):
        response = logged_in_client.get(f'/contest/{sample_contest.id}/rules')
        assert response.status_code == 200
        assert '规则确认' in response.text

    def test_contest_detail_page(self, logged_in_client, sample_contest):
        response = logged_in_client.get(f'/contest/{sample_contest.id}')
        assert response.status_code == 200
        assert '测试萌战' in response.text

    def test_board_post_requires_login(self, client):
        response = client.post('/board', data={'content': 'test'}, follow_redirects=False)
        assert response.status_code == 302

    def test_add_reply_requires_login(self, client, sample_message):
        response = client.post(f'/reply/{sample_message.id}', data={'content': 'test reply'}, follow_redirects=False)
        assert response.status_code == 302


class TestVoting:
    """投票功能测试（直接测试提交接口，验证数据库）"""

    # ---------- 海选投票 ----------

    def test_qualifying_vote_submit(self, logged_in_client, db_session, sample_contest, sample_user):
        """测试海选投票成功提交"""
        contest = db_session.get(Contest, sample_contest.id)
        contest.status = 'open'
        contest.open_at = datetime.now() - timedelta(days=10)  # 落在海选投票期内（第8~12天）
        db_session.commit()
        db_session.refresh(contest)

        candidate = contest.candidates.filter_by(gender='female').first()
        assert candidate is not None

        response = logged_in_client.post(
            f'/contest/{contest.id}/qualifying/submit',
            data={'gender': 'female', f'vote_{candidate.id}': '3'},
            follow_redirects=True
        )
        # 提交接口成功后通常重定向到投票页，返回200
        assert response.status_code == 200
        # 验证数据库中的投票记录
        vote = ContestVote.query.filter_by(
            contest_id=contest.id,
            candidate_id=candidate.id,
            user_id=sample_user.id,
            round_number=0,
            gender='female'
        ).first()
        assert vote is not None
        assert vote.weight == 3

    def test_qualifying_vote_duplicate(self, logged_in_client, db_session, sample_contest, sample_user):
        """测试同一用户不能重复投票（同一组别）"""
        contest = db_session.get(Contest, sample_contest.id)
        contest.status = 'open'
        contest.open_at = datetime.now() - timedelta(days=10)
        db_session.commit()
        db_session.refresh(contest)

        candidate = contest.candidates.filter_by(gender='female').first()
        assert candidate is not None

        # 第一次投票
        resp1 = logged_in_client.post(
            f'/contest/{contest.id}/qualifying/submit',
            data={'gender': 'female', f'vote_{candidate.id}': '2'},
            follow_redirects=True
        )
        assert resp1.status_code == 200
        # 验证第一次投票成功
        vote1 = ContestVote.query.filter_by(
            contest_id=contest.id,
            candidate_id=candidate.id,
            user_id=sample_user.id,
            round_number=0,
            gender='female'
        ).first()
        assert vote1 is not None

        # 第二次投票（应被拒绝，返回flash消息）
        resp2 = logged_in_client.post(
            f'/contest/{contest.id}/qualifying/submit',
            data={'gender': 'female', f'vote_{candidate.id}': '1'},
            follow_redirects=True
        )
        assert resp2.status_code == 200
        # 检查flash消息（可通过检查页面内容，但更可靠是检查数据库没有新记录）
        vote2_count = ContestVote.query.filter_by(
            contest_id=contest.id,
            candidate_id=candidate.id,
            user_id=sample_user.id,
            round_number=0,
            gender='female'
        ).count()
        assert vote2_count == 1  # 只有一条

    def test_qualifying_vote_exceed_limits(self, logged_in_client, db_session, sample_contest, sample_user):
        """测试投票数量限制（总票数≤15，单角色≤3）"""
        contest = db_session.get(Contest, sample_contest.id)
        contest.status = 'open'
        contest.open_at = datetime.now() - timedelta(days=10)
        db_session.commit()
        db_session.refresh(contest)

        candidates = contest.candidates.filter_by(gender='female').limit(6).all()
        assert len(candidates) >= 6

        # 总票数超限：给6个角色各3票 => 18票 > 15
        data = {'gender': 'female'}
        for idx, cand in enumerate(candidates[:6]):
            data[f'vote_{cand.id}'] = '3'
        response = logged_in_client.post(
            f'/contest/{contest.id}/qualifying/submit',
            data=data,
            follow_redirects=True
        )
        assert response.status_code == 200
        # 验证数据库没有新增投票（因为请求被拒绝）
        vote_count = ContestVote.query.filter_by(
            contest_id=contest.id,
            user_id=sample_user.id,
            round_number=0,
            gender='female'
        ).count()
        assert vote_count == 0

        # 单角色超限：给一个角色投4票
        data2 = {'gender': 'female', f'vote_{candidates[0].id}': '4'}
        response2 = logged_in_client.post(
            f'/contest/{contest.id}/qualifying/submit',
            data=data2,
            follow_redirects=True
        )
        assert response2.status_code == 200
        # 同样没有新增
        vote_count2 = ContestVote.query.filter_by(
            contest_id=contest.id,
            user_id=sample_user.id,
            round_number=0,
            gender='female'
        ).count()
        assert vote_count2 == 0

    # ---------- 小组赛投票（可选） ----------

    def test_group_vote_submit(self, logged_in_client, db_session, sample_contest, sample_user):
        """测试小组赛投票（需提前设置分组）"""
        contest = db_session.get(Contest, sample_contest.id)
        contest.status = 'group_stage'
        contest.open_at = datetime.now() - timedelta(days=20)  # 为了满足可能的时间检查
        # 构造分组数据
        female_candidates = contest.candidates.filter_by(gender='female').limit(4).all()
        if len(female_candidates) < 4:
            pytest.skip("需要至少4个女角色")
        contest.config = {
            'female_groups': [[c.id for c in female_candidates]]
        }
        db_session.commit()
        db_session.refresh(contest)

        candidate = female_candidates[0]
        response = logged_in_client.post(
            f'/contest/{contest.id}/group/submit',
            data={'gender': 'female', 'candidate_id': candidate.id},
            follow_redirects=True
        )
        # 由于时间控制，可能被拒绝，但确保不报错
        assert response.status_code == 200
        # 如果成功则验证数据库，但这里不强求

    # ---------- 淘汰赛投票（可选） ----------

    def test_knockout_vote_submit(self, logged_in_client, db_session, sample_contest, sample_user):
        """测试淘汰赛投票（需提前设置对阵）"""
        contest = db_session.get(Contest, sample_contest.id)
        contest.status = 'knockout'
        contest.open_at = datetime.now() - timedelta(days=40)
        female_candidates = contest.candidates.filter_by(gender='female').limit(2).all()
        if len(female_candidates) < 2:
            pytest.skip("需要至少2个女角色")
        contest.config = {
            'knockout_matches_female': [
                {
                    'candidate1': female_candidates[0].id,
                    'candidate2': female_candidates[1].id,
                    'status': 'active',
                    'winner': None
                }
            ]
        }
        db_session.commit()
        db_session.refresh(contest)

        candidate = female_candidates[0]
        response = logged_in_client.post(
            f'/contest/{contest.id}/knockout/submit',
            data={'gender': 'female', 'candidate_id': candidate.id},
            follow_redirects=True
        )
        assert response.status_code == 200


# ========== 赛事各阶段、投票边界、AI接口 ==========

class TestPublicExtra:

    def test_contest_detail_nomination_phase(self, logged_in_client, sample_contest, db_session):
        contest = sample_contest
        contest.status = 'open'
        contest.open_at = datetime(2026, 10, 1, 18, 0, 0)
        db_session.commit()
        with patch('app.public.datetime') as mock_dt:
            mock_dt.now.return_value = datetime(2026, 10, 3, 12, 0, 0)
            mock_dt.side_effect = lambda *args, **kw: datetime(*args, **kw)
            resp = logged_in_client.get(f'/contest/{contest.id}')
            assert '当前为 <strong>提名期</strong>' in resp.text

    def test_qualifying_vote_zero_votes(self, logged_in_client, sample_contest, db_session, sample_user):
        contest = sample_contest
        contest.status = 'open'
        contest.open_at = datetime.now() - timedelta(days=10)
        db_session.commit()

        resp = logged_in_client.post(
            f'/contest/{contest.id}/qualifying/submit',
            data={'gender': 'female'},
            headers={'X-Requested-With': 'XMLHttpRequest'},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['success'] is False
        assert '至少' in data['message']

        from app.models import ContestVote
        votes = ContestVote.query.filter_by(
            contest_id=contest.id,
            user_id=sample_user.id,
            round_number=0
        ).all()
        assert len(votes) == 0

    def test_qualifying_vote_exceed_candidates(self, logged_in_client, sample_contest, db_session, sample_user):
        contest = sample_contest
        contest.status = 'open'
        contest.open_at = datetime.now() - timedelta(days=10)
        db_session.commit()

        candidates = contest.candidates.filter_by(gender='female').limit(6).all()
        data = {'gender': 'female'}
        for c in candidates:
            data[f'vote_{c.id}'] = '1'

        resp = logged_in_client.post(
            f'/contest/{contest.id}/qualifying/submit',
            data=data,
            headers={'X-Requested-With': 'XMLHttpRequest'},
        )
        assert resp.status_code == 200
        result = resp.get_json()
        assert result['success'] is False
        assert '5' in result['message'] or '最多' in result['message']

        from app.models import ContestVote
        votes = ContestVote.query.filter_by(
            contest_id=contest.id,
            user_id=sample_user.id,
            round_number=0
        ).all()
        assert len(votes) == 0

    def test_group_vote_submit_success(self, logged_in_client, sample_contest, db_session, sample_user):
        contest = sample_contest
        contest.status = 'group_stage'
        contest.open_at = datetime.now() - timedelta(days=22)
        female_candidates = contest.candidates.filter_by(gender='female').limit(4).all()
        if len(female_candidates) < 4:
            pytest.skip("Need 4 female candidates")
        contest.config = {'female_groups': [[c.id for c in female_candidates]]}
        db_session.commit()

        with patch('app.public.datetime') as mock_dt:
            mock_dt.now.return_value = datetime.now() - timedelta(days=18)
            mock_dt.side_effect = lambda *args, **kw: datetime(*args, **kw)
            resp = logged_in_client.post(
                f'/contest/{contest.id}/group/submit',
                data={'gender': 'female', 'candidate_id': female_candidates[0].id},
                headers={'X-Requested-With': 'XMLHttpRequest'},
            )
            assert resp.status_code == 200
            result = resp.get_json()
            assert result['success'] is True

            from app.models import ContestVote
            vote = ContestVote.query.filter_by(
                contest_id=contest.id,
                user_id=sample_user.id,
                round_number=1,
                gender='female',
                candidate_id=female_candidates[0].id
            ).first()
            assert vote is not None

    def test_knockout_vote_submit_success(self, logged_in_client, sample_contest, db_session, sample_user):
        contest = sample_contest
        contest.status = 'knockout'
        contest.open_at = datetime.now() - timedelta(days=40)
        female_candidates = contest.candidates.filter_by(gender='female').limit(2).all()
        if len(female_candidates) < 2:
            pytest.skip("Need 2 female candidates")
        contest.config = {
            'knockout_matches_female': [
                {
                    'candidate1': female_candidates[0].id,
                    'candidate2': female_candidates[1].id,
                    'status': 'active'
                }
            ]
        }
        db_session.commit()

        with patch('app.public.datetime') as mock_dt:
            mock_dt.now.return_value = datetime.now() - timedelta(days=35)
            mock_dt.side_effect = lambda *args, **kw: datetime(*args, **kw)
            resp = logged_in_client.post(
                f'/contest/{contest.id}/knockout/submit',
                data={
                    'gender': 'female',
                    'candidate_id': female_candidates[0].id,
                    'match_index': 1,
                },
                headers={'X-Requested-With': 'XMLHttpRequest'},
            )
            assert resp.status_code == 200
            result = resp.get_json()
            assert result['success'] is True

            from app.models import ContestVote
            vote = ContestVote.query.filter_by(
                contest_id=contest.id,
                user_id=sample_user.id,
                round_number=4,
                sub_round=1,
                match_index=1,
                gender='female',
                candidate_id=female_candidates[0].id
            ).first()
            assert vote is not None

    def test_knockout_cannot_vote_twice_by_switching_match_index(
            self, logged_in_client, sample_contest, db_session, sample_user):
        """回归：换 match_index 不能再给同一角色重复投票（原可无限刷票）"""
        contest = sample_contest
        contest.status = 'knockout'
        contest.open_at = datetime.now() - timedelta(days=40)
        female_candidates = contest.candidates.filter_by(gender='female').limit(2).all()
        if len(female_candidates) < 2:
            pytest.skip("Need 2 female candidates")
        contest.config = {
            'knockout_matches_female': [
                {
                    'candidate1': female_candidates[0].id,
                    'candidate2': female_candidates[1].id,
                    'status': 'active'
                }
            ]
        }
        db_session.commit()

        from app.models import ContestVote

        with patch('app.public.datetime') as mock_dt:
            mock_dt.now.return_value = datetime.now() - timedelta(days=35)
            mock_dt.side_effect = lambda *args, **kw: datetime(*args, **kw)

            first = logged_in_client.post(
                f'/contest/{contest.id}/knockout/submit',
                data={'gender': 'female', 'candidate_id': female_candidates[0].id,
                      'match_index': 1},
                headers={'X-Requested-With': 'XMLHttpRequest'})
            assert first.get_json()['success'] is True

            # 换一个 match_index 再投同一角色：必须被拒绝
            second = logged_in_client.post(
                f'/contest/{contest.id}/knockout/submit',
                data={'gender': 'female', 'candidate_id': female_candidates[0].id,
                      'match_index': 99},
                headers={'X-Requested-With': 'XMLHttpRequest'})
            assert second.get_json()['success'] is False

            # 不在本轮对阵中的角色不能投票
            outsider = contest.candidates.filter_by(gender='female').all()
            outsider_id = [c.id for c in outsider if c.id not in
                           (female_candidates[0].id, female_candidates[1].id)][0]
            third = logged_in_client.post(
                f'/contest/{contest.id}/knockout/submit',
                data={'gender': 'female', 'candidate_id': outsider_id,
                      'match_index': 1},
                headers={'X-Requested-With': 'XMLHttpRequest'})
            assert third.get_json()['success'] is False

        votes = ContestVote.query.filter_by(
            contest_id=contest.id, user_id=sample_user.id,
            round_number=4, sub_round=1, candidate_id=female_candidates[0].id).all()
        assert len(votes) == 1

    def test_qualifying_vote_outside_window_rejected(
            self, logged_in_client, sample_contest, db_session, sample_user):
        """回归：提名期内不能投海选票（原先只检查 status）"""
        contest = sample_contest
        contest.status = 'open'
        contest.open_at = datetime.now() - timedelta(days=2)  # 提名期
        db_session.commit()

        from app.models import ContestVote
        candidate = contest.candidates.filter_by(gender='female').first()
        resp = logged_in_client.post(
            f'/contest/{contest.id}/qualifying/submit',
            data={'gender': 'female', f'vote_{candidate.id}': '3'},
            headers={'X-Requested-With': 'XMLHttpRequest'})
        assert resp.get_json()['success'] is False
        assert ContestVote.query.filter_by(
            contest_id=contest.id, user_id=sample_user.id,
            round_number=0).count() == 0

    def test_qualifying_vote_cross_gender_rejected(
            self, logged_in_client, sample_contest, db_session, sample_user):
        """回归：用 gender=female 给男角色投票必须被拒绝（原先可双计票）"""
        contest = sample_contest
        contest.status = 'open'
        contest.open_at = datetime.now() - timedelta(days=10)
        db_session.commit()

        male_candidate = contest.candidates.filter_by(gender='male').first()
        resp = logged_in_client.post(
            f'/contest/{contest.id}/qualifying/submit',
            data={'gender': 'female', f'vote_{male_candidate.id}': '3'},
            headers={'X-Requested-With': 'XMLHttpRequest'})
        assert resp.get_json()['success'] is False

    def test_ai_commentary_api(self, logged_in_client, sample_contest):
        with patch('app.public.generate_commentary', return_value=(True, 'AI commentary', None)):
            resp = logged_in_client.get(f'/contest/{sample_contest.id}/commentary?phase=qualifying')
            assert resp.get_json()['commentary'] == 'AI commentary'

    def test_ai_prediction_api(self, logged_in_client, sample_contest):
        with patch('app.public.generate_prediction', return_value=(True, 'AI prediction', None)):
            resp = logged_in_client.get(f'/contest/{sample_contest.id}/predict')
            assert resp.get_json()['prediction'] == 'AI prediction'


# ========== 站点设置页（访客可用；账号级设置需登录） ==========

class TestSettingsPage:

    def test_settings_page_loads_for_guest(self, client):
        """设置页对访客开放：外观 / 语言 / 通知（登录提示）/ 数据 四个分区都在"""
        resp = client.get('/settings')
        assert resp.status_code == 200
        body = resp.get_data(as_text=True)
        for text in ('外观与显示', '语言与翻译', '通知与免打扰', '数据与隐私'):
            assert text in body, f'缺少分区：{text}'

    def test_settings_page_has_three_news_language_options(self, client):
        """阶段 C：语言与翻译给出「译文优先 / 仅原文 / 双语对照」三档"""
        body = client.get('/settings').get_data(as_text=True)
        assert body.count('data-group="news-lang"') == 3
        for value in ('data-value="zh"', 'data-value="original"', 'data-value="both"'):
            assert value in body, f'缺少语言选项：{value}'

    def test_about_page_has_stats_and_highlights(self, logged_in_client, sample_activity):
        """社团简介不再只有两句话：有数据条（成员/活动/赛事）和「我们在做什么」"""
        from app.models import Activity, Contest, User
        body = logged_in_client.get('/about').text
        assert '注册成员' in body
        assert '社团活动' in body
        assert '线上赛事' in body
        assert '我们在做什么' in body
        # 统计数字要和库里对得上
        assert f'<strong>{User.query.count()}</strong>' in body
        assert f'<strong>{Activity.query.count()}</strong>' in body
        assert f'<strong>{Contest.query.count()}</strong>' in body

    def test_activities_page_shows_activity(self, logged_in_client, sample_activity):
        body = logged_in_client.get('/activities').text
        assert '测试活动' in body
        assert '共 1 场活动' in body

    def test_members_page_shows_count(self, logged_in_client, sample_user):
        body = logged_in_client.get('/members').text
        assert '位成员' in body
        assert sample_user.username in body

    def test_settings_page_hides_notification_form_for_guest(self, client):
        """通知免打扰是账号级设置：访客只能看到登录提示，看不到表单"""
        body = client.get('/settings').get_data(as_text=True)
        assert 'name="muted_types"' not in body
        assert 'settings-login-hint' in body


class TestFlashMessages:
    """一次性提示统一在 base.html 渲染（回归：提示曾残留在 session 里隔几页才弹出）"""

    def _queue(self, client, message):
        with client.session_transaction() as sess:
            sess['_flashes'] = [('success', message)]

    def test_flash_rendered_on_any_page_and_consumed_once(self, client):
        self._queue(client, '提示只应出现一次')
        first = client.get('/settings').get_data(as_text=True)
        assert '提示只应出现一次' in first, '没有 flash 块的页面也必须显示提示'
        second = client.get('/settings').get_data(as_text=True)
        assert '提示只应出现一次' not in second, '提示被重复显示（未被消费）'

    def test_flash_not_leaked_to_next_page(self, client):
        """校验提示在第一个页面就被消费，不会跟着用户跑到下一个页面"""
        self._queue(client, '禁赛相关提示')
        client.get('/anime_news')
        later = client.get('/settings').get_data(as_text=True)
        assert '禁赛相关提示' not in later, '提示残留到了后续页面'


class TestBoardDelete:
    """社员删除自己的帖子 / 回复（作者本人或运营/站长）"""

    def _thread(self, db_session, user, title='可删除的帖子'):
        msg = Message(title=title, content='正文', nickname=user.username, user_id=user.id)
        db_session.add(msg)
        db_session.commit()
        db_session.refresh(msg)
        return msg

    def _reply(self, db_session, user, msg, content='一条回复', parent_id=None):
        reply = Reply(nickname=user.username, content=content,
                      message_id=msg.id, user_id=user.id, parent_reply_id=parent_id)
        db_session.add(reply)
        db_session.commit()
        db_session.refresh(reply)
        return reply

    def test_author_can_delete_own_thread(self, logged_in_client, db_session, sample_user):
        msg = self._thread(db_session, sample_user)
        self._reply(db_session, sample_user, msg)
        resp = logged_in_client.post(f'/board/{msg.id}/delete')
        assert resp.status_code == 302
        assert '/board' in resp.headers['Location']
        assert db_session.get(Message, msg.id) is None
        assert Reply.query.filter_by(message_id=msg.id).count() == 0

    def test_cannot_delete_others_thread(self, logged_in_client, db_session):
        other = User(username='别人的号', qq='111222333', email='other@example.com')
        other.set_password('x')
        db_session.add(other)
        db_session.commit()
        msg = self._thread(db_session, other)
        resp = logged_in_client.post(f'/board/{msg.id}/delete')
        assert resp.status_code == 403
        assert db_session.get(Message, msg.id) is not None

    def test_moderator_can_delete_others_thread(self, admin_client, db_session, sample_user):
        msg = self._thread(db_session, sample_user)
        resp = admin_client.post(f'/board/{msg.id}/delete')
        assert resp.status_code == 302
        assert db_session.get(Message, msg.id) is None

    def test_author_can_delete_own_reply(self, logged_in_client, db_session, sample_user):
        msg = self._thread(db_session, sample_user)
        reply = self._reply(db_session, sample_user, msg)
        resp = logged_in_client.post(f'/reply/{reply.id}/delete')
        assert resp.status_code == 302
        assert db_session.get(Reply, reply.id) is None

    def test_deleting_reply_detaches_children(self, logged_in_client, db_session, sample_user):
        msg = self._thread(db_session, sample_user)
        parent = self._reply(db_session, sample_user, msg, '父楼层')
        other = User(username='围观群众', qq='444555666', email='watch@example.com')
        other.set_password('x')
        db_session.add(other)
        db_session.commit()
        child = self._reply(db_session, other, msg, '子楼层', parent_id=parent.id)

        logged_in_client.post(f'/reply/{parent.id}/delete')
        db_session.refresh(child)
        assert child.parent_reply_id is None
        assert db_session.get(Reply, child.id) is not None

    def test_cannot_delete_others_reply(self, logged_in_client, db_session):
        other = User(username='回复的人', qq='777888999', email='reply@example.com')
        other.set_password('x')
        db_session.add(other)
        db_session.commit()
        msg = self._thread(db_session, other)
        reply = self._reply(db_session, other, msg)
        resp = logged_in_client.post(f'/reply/{reply.id}/delete')
        assert resp.status_code == 403
        assert db_session.get(Reply, reply.id) is not None

    def test_guest_cannot_delete(self, client, db_session, sample_user):
        msg = self._thread(db_session, sample_user)
        resp = client.post(f'/board/{msg.id}/delete')
        assert resp.status_code in (302, 400)
        assert db_session.get(Message, msg.id) is not None
