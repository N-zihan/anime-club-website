#!/usr/bin/env python
"""简化视频代理模块 - 去掉 aiohttp 依赖"""

import re

# 读取文件
with open('app/video_proxy.py', 'r', encoding='utf-8') as f:
    content = f.read()

# 1. 替换 async def 为 sync def
content = content.replace('async def _fetch_video_content', 'def _fetch_video_content')
content = content.replace('async with session.get(url', 'with requests.get(url')
content = content.replace('aiohttp.ClientSession', 'requests.Session')
content = content.replace('aiohttp.timeout', '30')
content = content.replace('aiohttp.ClientError', 'requests.RequestException')

# 移除 asyncio 相关导入
content = re.sub(r'import asyncio', '', content)
content = re.sub(r'from concurrent.futures import ThreadPoolExecutor', '', content)
content = re.sub(r'from functools import wraps', '', content)
content = re.sub(r'from datetime import.*$', '', content)

# 2. 把 aiohttp.AsyncContextManager 相关的 async/await 改回同步
content = re.sub(
    r'async with aiohttp\.ClientSession\(\) as session:',
    's = requests.Session()',
    content
)

# 3. 替换 async def _proxy() 为 sync def _proxy()
content = re.sub(r'async def _proxy\(\):', 'def _proxy():', content)
content = re.sub(r'\.run_until_complete\(_proxy\(\)\)', '.run(_proxy())', content)
content = re.sub(r'loop\.close\(\)', '', content)

# 4. 检查内容是否还包含 async 或 aiohttp
if 'async ' in content or 'aiohttp' in content:
    print('Warning: 仍包含 async 或 aiohttp')
    print(content[-500:])
else:
    print('成功移除所有 aiohttp 相关代码')

# 5. 检查仍使用的异步库
async_patterns = [
    'asyncio', 'await', 'async for', 'for await',
]
for pattern in async_patterns:
    if pattern in content:
        print(f'警告：仍包含 {pattern}')

# 6. 写入文件
with open('app/video_proxy.py', 'w', encoding='utf-8') as f:
    f.write(content)

print('完成重构')