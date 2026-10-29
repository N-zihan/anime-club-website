"""淘汰赛票数进度条颜色必须和左右投票按钮一致（左粉右蓝）。"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_knockout_bar_uses_knockout_modifier():
    tpl = (ROOT / 'templates' / 'contest_knockout_vote.html').read_text(encoding='utf-8')
    assert 'vote-vs-bar-knockout' in tpl


def test_knockout_bar_colors_match_button_colors():
    css = (ROOT / 'static' / 'css' / 'style.css').read_text(encoding='utf-8')
    # 左侧按钮 #e91e63（粉）→ 左条粉色；右侧按钮 #2196f3（蓝）→ 右条蓝色
    assert '.vote-vs-bar-knockout .vote-vs-left' in css
    assert '.vote-vs-bar-knockout .vote-vs-right' in css
    left_rule = css.split('.vote-vs-bar-knockout .vote-vs-left')[1].split('}')[0]
    right_rule = css.split('.vote-vs-bar-knockout .vote-vs-right')[1].split('}')[0]
    assert '#e91e63' in left_rule
    assert '#2196f3' in right_rule
