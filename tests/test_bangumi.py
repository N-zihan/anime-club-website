"""bangumi 模块测试：mock requests，覆盖成功/失败/空响应分支（原覆盖率 9%）。"""
from unittest.mock import MagicMock, patch

from app import bangumi


def _resp(payload):
    resp = MagicMock()
    resp.json.return_value = payload
    return resp


class TestFormatItem:
    def test_minimal_item(self):
        item = bangumi._format_item({'id': 1, 'name': '测试番'})  # pylint: disable=protected-access
        assert item['id'] == 1
        assert item['name'] == '测试番'
        assert item['cover'] == ''
        assert item['score'] == 0

    def test_full_item_prefers_chinese_name(self):
        item = bangumi._format_item({  # pylint: disable=protected-access
            'id': 2, 'name': 'original', 'name_cn': '中文名', 'summary': ' 简介 ',
            'images': {'common': 'https://img/c.jpg'},
            'rating': {'score': 8.5}, 'rank': 10, 'date': '2024-01-01', 'eps': 12,
        })
        assert item['name'] == '中文名'
        assert item['original_name'] == 'original'
        assert item['summary'] == '简介'
        assert item['cover'] == 'https://img/c.jpg'
        assert item['score'] == 8.5
        assert item['rank'] == 10
        assert item['eps'] == 12


class TestGetSubjects:
    def test_rank_sort_success(self):
        payload = {'data': [{'id': 1, 'name': 'A'}]}
        with patch('app.bangumi.requests.get', return_value=_resp(payload)):
            items = bangumi.get_subjects(offset=0, limit=10, sort='rank')
        assert items and items[0]['name'] == 'A'

    def test_list_payload_supported(self):
        with patch('app.bangumi.requests.get', return_value=_resp([{'id': 2, 'name': 'B'}])):
            items = bangumi.get_subjects(sort='date')
        assert items and items[0]['id'] == 2

    def test_bad_payload_returns_empty(self):
        with patch('app.bangumi.requests.get', return_value=_resp({'data': 'oops'})):
            assert bangumi.get_subjects() == []

    def test_exception_returns_empty(self):
        with patch('app.bangumi.requests.get', side_effect=RuntimeError('boom')):
            assert bangumi.get_subjects() == []

    def test_heat_sort_uses_post(self):
        payload = {'data': [{'id': 3, 'name': 'C'}]}
        with patch('app.bangumi.requests.post', return_value=_resp(payload)) as mock_post:
            items = bangumi.get_subjects(sort='heat')
        assert items and mock_post.called


class TestSearchSubjects:
    def test_success_returns_items_and_total(self):
        payload = {'data': [{'id': 3, 'name': 'B'}], 'total': 42}
        with patch('app.bangumi.requests.post', return_value=_resp(payload)):
            items, total = bangumi.search_subjects('B')
        assert items and items[0]['name'] == 'B'
        assert total == 42

    def test_empty_data(self):
        with patch('app.bangumi.requests.post', return_value=_resp({'data': [], 'total': 0})):
            items, total = bangumi.search_subjects('x')
        assert items == [] and total == 0

    def test_exception(self):
        with patch('app.bangumi.requests.post', side_effect=RuntimeError('boom')):
            assert bangumi.search_subjects('x') == ([], 0)


class TestSubjectDetail:
    def test_found_with_staff(self):
        payload = {
            'id': 5, 'name': 'orig', 'name_cn': '中文',
            'images': {'large': 'https://img/l.jpg'},
            'rating': {'score': 9.1, 'rank': 3, 'total': 1000},
            'eps': 12, 'platform': 'TV',
            'infobox': [
                {'key': '导演', 'value': '某导演'},
                {'key': '其他', 'value': '忽略'},
                {'key': '制作', 'value': [{'v': 'A'}, {'v': 'B'}]},
            ],
        }
        with patch('app.bangumi.requests.get', return_value=_resp(payload)):
            detail = bangumi.get_subject_detail(5)
        assert detail['name'] == '中文'
        assert detail['cover'] == 'https://img/l.jpg'
        assert detail['score'] == 9.1
        assert detail['rank'] == 3
        assert {s['key'] for s in detail['staff']} == {'导演', '制作'}
        assert detail['staff'][1]['value'] == 'A、B'

    def test_not_found(self):
        with patch('app.bangumi.requests.get', return_value=_resp({})):
            assert bangumi.get_subject_detail(5) is None

    def test_exception(self):
        with patch('app.bangumi.requests.get', side_effect=RuntimeError('boom')):
            assert bangumi.get_subject_detail(5) is None


class TestCharacters:
    def test_success_sorts_main_character_first(self):
        payload = [
            {'id': 1, 'name': '配角', 'relation': '配角',
             'images': {'grid': 'https://img/g.jpg'}, 'actors': [{'name': 'CV1'}]},
            {'id': 2, 'name': '主角', 'relation': '主角',
             'images': {'medium': 'https://img/m.jpg'}, 'actors': [{'name': 'CV2'}]},
        ]
        with patch('app.bangumi.requests.get', return_value=_resp(payload)):
            chars = bangumi.get_subject_characters(1)
        assert chars[0]['name'] == '主角'
        assert chars[0]['cv'] == 'CV2'
        assert chars[1]['image'] == 'https://img/g.jpg'

    def test_non_list_returns_empty(self):
        with patch('app.bangumi.requests.get', return_value=_resp({'data': []})):
            assert bangumi.get_subject_characters(1) == []

    def test_exception(self):
        with patch('app.bangumi.requests.get', side_effect=RuntimeError('boom')):
            assert bangumi.get_subject_characters(1) == []

    def test_character_detail(self):
        payload = {
            'id': 9, 'name': '角色9', 'summary': '介绍',
            'images': {'large': 'https://img/c.jpg'},
            'gender': '女', 'birthday': '1月1日',
            'actors': [{'name': 'CV9'}],
            'infobox': [{'key': '简体中文名', 'value': '角色九'}],
        }
        with patch('app.bangumi.requests.get', return_value=_resp(payload)):
            data = bangumi.get_character_detail(9)
        assert data['name'] == '角色九'
        assert data['original_name'] == '角色9'
        assert data['cv'] == 'CV9'
        assert data['extra']['简体中文名'] == '角色九'

    def test_character_detail_missing(self):
        with patch('app.bangumi.requests.get', return_value=_resp({'name': '无 id'})):
            assert bangumi.get_character_detail(9) is None
