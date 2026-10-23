"""changelog 模块纯函数测试（此前覆盖率 0%）。"""
from app import changelog


class TestShouldShow:
    def test_empty_message_hidden(self):
        assert changelog.should_show('') is False
        assert changelog.should_show('   ') is False

    def test_internal_commits_hidden(self):
        for msg in ('chore: bump deps', 'ci: fix workflow', 'test: add cases',
                    'docs: update readme', 'style: reformat', 'Merge branch main'):
            assert changelog.should_show(msg) is False, msg

    def test_code_cleanup_keywords_hidden(self):
        for msg in ('feat: 整理 import', 'fix: 修复注释', 'refactor: 变量名调整'):
            assert changelog.should_show(msg) is False, msg

    def test_user_facing_commits_visible(self):
        for msg in ('feat: 新增追番清单', 'fix: 修复移动端溢出',
                    'perf: 优化列表查询', 'refactor: 拆分 public.py',
                    '新增帖子编辑功能', '优化首页加载'):
            assert changelog.should_show(msg) is True, msg


class TestExtractTag:
    def test_english_prefixes(self):
        assert changelog._extract_tag('feat: x') == 'feat'  # pylint: disable=protected-access
        assert changelog._extract_tag('fix：x') == 'fix'  # pylint: disable=protected-access
        assert changelog._extract_tag('perf: x') == 'perf'  # pylint: disable=protected-access
        assert changelog._extract_tag('refactor: x') == 'refactor'  # pylint: disable=protected-access

    def test_chinese_prefixes(self):
        assert changelog._extract_tag('新增功能') == 'feat'  # pylint: disable=protected-access
        assert changelog._extract_tag('修复 bug') == 'fix'  # pylint: disable=protected-access
        assert changelog._extract_tag('优化性能') == 'perf'  # pylint: disable=protected-access
        assert changelog._extract_tag('重构代码') == 'refactor'  # pylint: disable=protected-access
        assert changelog._extract_tag('更新文档') == 'feat'  # pylint: disable=protected-access

    def test_other(self):
        assert changelog._extract_tag('随便写点什么') == 'other'  # pylint: disable=protected-access


class TestCleanMessage:
    def test_strips_prefix(self):
        assert changelog._clean_message('feat: 新增追番') == '新增追番'  # pylint: disable=protected-access
        assert changelog._clean_message('fix：修复溢出') == '修复溢出'  # pylint: disable=protected-access

    def test_unknown_prefix_kept(self):
        assert changelog._clean_message('随便写点什么') == '随便写点什么'  # pylint: disable=protected-access


class TestGetAllCommits:
    def test_cached_result_returned(self, monkeypatch):
        cached = [{'sha': 'x', 'message': 'feat: 测试'}]
        monkeypatch.setitem(changelog._cache, 'all_data', cached)  # pylint: disable=protected-access
        from datetime import datetime, timedelta  # pylint: disable=import-outside-toplevel
        monkeypatch.setitem(changelog._cache, 'all_expires', datetime.now() + timedelta(hours=1))  # pylint: disable=protected-access
        assert changelog.get_all_commits() == cached
