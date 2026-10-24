"""文档 / 代码守卫：不写死真实线上域名；部署文档必须与仓库里的配置一致。

用户要求（v4.31.9 起）：
- 文档中不得出现本社团真实域名，一律用占位符；
- 部署文档「不准无中生有」，写的配置项必须是仓库里真实存在的；
- 代码 / 模板 / 静态文件也不写死域名（v4.32.0 起 robots / sitemap / 新闻上游都按请求域名推导）。
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REAL_DOMAIN = 'nanyi-anime-club.top'
PLACEHOLDER = 'your-site.example.com'


def _doc_files():
    files = sorted((ROOT / 'docs').rglob('*.md'))
    files.append(ROOT / 'README.md')
    files.append(ROOT / '.env.example')
    return files


def test_docs_have_no_real_domain():
    offenders = [
        str(p.relative_to(ROOT))
        for p in _doc_files()
        if REAL_DOMAIN in p.read_text(encoding='utf-8')
    ]
    assert not offenders, f'文档里出现真实域名，请换成占位符: {offenders}'


def test_env_example_has_no_real_domain():
    assert REAL_DOMAIN not in (ROOT / '.env.example').read_text(encoding='utf-8')


def test_deployment_doc_uses_placeholder():
    text = (ROOT / 'docs' / 'deployment.md').read_text(encoding='utf-8')
    assert PLACEHOLDER in text
    assert REAL_DOMAIN not in text


def test_deployment_doc_matches_vercel_json():
    """部署文档里贴的 vercel.json 关键字段必须和仓库实际文件一致。"""
    cfg = json.loads((ROOT / 'vercel.json').read_text(encoding='utf-8'))
    text = (ROOT / 'docs' / 'deployment.md').read_text(encoding='utf-8')

    build = cfg['builds'][0]
    assert build['src'] in text
    assert str(build['config']['maxDuration']) in text
    assert build['config']['pythonVersion'] in text
    assert cfg['regions'][0] in text
    assert cfg['routes'][0]['dest'] in text
    assert cfg['crons'][0]['path'] in text
    assert cfg['crons'][0]['schedule'] in text


def test_code_static_templates_have_no_real_domain():
    """代码 / 模板 / 静态文件里不能写死线上域名（运行时按请求域名推导或走环境变量）。"""
    targets = [ROOT / 'app', ROOT / 'static', ROOT / 'templates',
               ROOT / 'run.py', ROOT / 'init.py']
    suffixes = {'.py', '.html', '.js', '.css', '.txt', '.xml', '.json', '.webmanifest'}
    offenders = []
    for target in targets:
        if target.is_file():
            paths = [target]
        else:
            paths = [p for p in target.rglob('*') if p.is_file() and p.suffix in suffixes]
        for path in paths:
            try:
                text = path.read_text(encoding='utf-8')
            except (UnicodeDecodeError, OSError):
                continue
            if REAL_DOMAIN in text:
                offenders.append(str(path.relative_to(ROOT)))
    assert not offenders, f'代码 / 模板 / 静态文件里写死了线上域名: {offenders}'
