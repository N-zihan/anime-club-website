"""
视频代理层【当前未启用 · NOT ENABLED】
====================================

⚠️ 本文件当前状态（请先读这段再决定是否使用）：
  - **未启用**：应用启动时不会注册本模块的任何路由；若注册开关被打开，
    `app/__init__.py` 也会用 try/except 兜住导入失败，不影响主站启动。
  - **不在版本控制**：`.gitignore` 已显式排除 `app/video_proxy.py`，
    因此它不会进仓库、也不会部署到 Vercel 生产环境。
  - **依赖缺失**：本文件顶层 `import httpx`，而 `requirements.txt`
    并未包含 aiohttp。若某个环境没装 aiohttp，导入本模块会直接
    `ModuleNotFoundError`（历史上曾因此造成"整站起不来"）。
  - **启用前必须由用户显式决定**（不是默认行为），并且需要：
      1) 在 requirements.txt 增加 aiohttp（本文件所在任务明确禁止修改）；
      2) 补齐 Range / 流式响应（当前实现是整段进内存，见 _fetch_video_content）；
      3) 合规评审：见下方"合规提醒"。

【合规提醒】本文件原注释中出现过"DRM 移除 / 解密受保护视频"之类的表述
（见本文件历史版本的"视频内容代理（防盗链/DRM移除）"与 `_process_video_content`
的 docstring）。**绕过 DRM、破解会员/付费墙、规避地理限制属于违反平台服务条款，
在多数司法辖区还可能构成侵权或违法行为。本项目不应提供此类能力。**
`_process_video_content` 现在的实现只是原样透传（未做任何解密），如将来启用，
请删除一切涉及 DRM 绕过 / 会员绕过的描述与代码，仅保留合法的公开视频嵌入。

【补充事实】服务端代理**无法解决**以下两类问题（这也是不建议启用它的原因）：
  - 域名本身被网络封锁：服务端与用户所在网络环境不同，代理不改变可达性；
  - YouTube 等平台使用 DASH + 签名 URL 分片：代理单个分片 URL 会因签名/时效
    失效而拿不到完整视频流。

------------------------------------------------------------------
原始模块说明（保留历史设计意图，仅供参考）

本模块为动漫社官网提供视频代理服务，解决直接访问视频资源时可能遇到的问题：

1. **播放权限限制** - 某些视频平台需要登录或会员权限
2. **地理区域限制** - 部分视频在特定地区不可访问  
3. **网络安全限制** - 企业/校园网络可能封锁视频平台
4. **防盗链保护** - 网站对直接嵌入视频设置了防盗链
5. **DRM 保护** - 需要解密才能播放的受保护视频

核心设计理念：
- **白名单机制**：可配置允许访问的视频主机列表
- **安全代理**：对所有访问进行SSR安全检查
- **缓存策略**：代理后的视频结果进行缓存，避免重复请求
- **错误处理**：对各种失败情况进行统一处理和日志记录
- **扩展性**：支持多种视频类型的提取和处理

主要功能模块：
1. 视频URL验证和提取
2. 视频内容代理（防盗链/DRM移除）
3. 视频下载和处理
4. 主机白名单管理
5. 统计和监控

使用方式：
    from .video_proxy import (
        proxy_video, download_video, process_video, validate_video_url,
        add_video_host, remove_video_host, get_video_hosts
    )

注意：
- 所有视频代理请求都将通过安全检查
- 代理结果会进行缓存，缓存时间根据视频类型不同而不同
- 禁止代理私有网络地址或内部资源
"""

import asyncio
import hashlib
import inspect
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from functools import wraps
from typing import Dict, List, Optional, Tuple, Any

import requests
from flask import abort, jsonify, request
from sqlalchemy import and_, func

from .models import db, VideoProxy, VideoHost, VideoCache
from .utils import is_public_http_url, safe_get, _is_blocked_ip
from .config import VIDEO_PROXY_CACHE_TTL, VIDEO_HOST_ALLOWLIST, PWA_THEME_COLOR

logger = logging.getLogger(__name__)

# 线程池用于异步视频处理
_executor = ThreadPoolExecutor(max_workers=4)

# ============================================================
# 常量定义和配置
# ============================================================

# 视频类型常量
VIDEO_TYPE_EMBED = 'embed'      # 嵌入播放
VIDEO_TYPE_DIRECT = 'direct'    # 直接链接
VIDEO_TYPE_META = 'meta'        # 元数据（如 og:video）

# 代理状态常量
PROXY_STATUS_PENDING = 'pending'    # 待处理
PROXY_STATUS_PROCESSING = 'processing'  # 处理中
PROXY_STATUS_COMPLETED = 'completed' # 已完成
PROXY_STATUS_FAILED = 'failed'      # 失败

# 支持的视频平台域名
VIDEO_PLATFORMS = {
    'youtube': {
        'patterns': [r'youtube\.com', r'youtu\.be', r'ya\.aa'],
        'video_path_patterns': [r'/watch', r'/embed', r'/v/', r'/shorts'],
        'embed_base': 'https://www.youtube.com/embed/',
        'player_base': 'https://www.youtube.com/embed/watch?v=',
    },
    'bilibili': {
        'patterns': [r'bilibili\.com', r'www\.bilibili\.com'],
        'video_path_patterns': [r'/video', r'/番剧', r'/电视剧'],
        'embed_base': 'https://www.bilibili.com/video/',
        'player_base': 'https://www.bilibili.com/video/',
    },
    'nicovideo': {
        'patterns': [r'nicovideo\.jp', r'www\.nicovideo\.jp'],
        'video_path_patterns': [r'/watch', r'/video'],
        'embed_base': 'https://www.nicovideo.jp/watch/',
        'player_base': 'https://www.nicovideo.jp/watch/',
    },
    'dailymotion': {
        'patterns': [r'dailymotion\.com', r'www\.dailymotion\.com'],
        'video_path_patterns': [r'/video', r'/playlist'],
        'embed_base': 'https://www.dailymotion.com/embed/video/',
        'player_base': 'https://www.dailymotion.com/video/',
    },
    'twitch': {
        'patterns': [r'twitch\.tv', r'www\.twitch\.tv'],
        'video_path_patterns': [r'/videos', r'/directory'],
        'embed_base': 'https://player.twitch.tv/?video=',
        'player_base': 'https://www.twitch.tv/videos/',
    },
}

# ============================================================
# 辅助函数
# ============================================================

def generate_cache_key(url: str, headers: Dict = None) -> str:
    """生成视频URL的缓存键"""
    key_data = url
    if headers:
        # 只考虑关键的请求头
        key_headers = {k: v for k, v in headers.items() 
                      if k.lower() in ['user-agent', 'accept', 'referer']}
        key_data += str(sorted(key_headers.items()))
    return hashlib.md5(key_data.encode()).hexdigest()
def is_video_platform(url: str) -> Tuple[bool, Optional[str]]:
    """检查URL是否属于支持的视频平台"""
    from urllib.parse import urlparse
    
    parsed = urlparse(url)
    hostname = parsed.netloc.lower()
    path = parsed.path.lower()
    
    for platform, config in VIDEO_PLATFORMS.items():
        for pattern in config['patterns']:
            if re.search(pattern, hostname):
                return True, platform
    return False, None
def extract_video_id(url: str) -> Optional[str]:
    """从视频URL中提取视频ID"""
    # YouTube
    youtube_patterns = [
        r'youtu\.be/([^/?&]+)',
        r'youtube\.com/watch\?v=([^&]+)',
        r'youtube\.com/embed/([^/?&]+)',
        r'youtube\.com/v/([^/?&]+)',
        r'ya\.aa/([^/?&]+)',
    ]
    
    for pattern in youtube_patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    
    # Bilibili
    bilibili_patterns = [
        r'bilibili\.com/video/([^/?&]+)',
        r'bilibili\.com/([^/?&]+)',
    ]
    
    for pattern in bilibili_patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    
    # NicoVideo
    nicovideo_patterns = [
        r'nicovideo\.jp/watch/([^/?&]+)',
    ]
    
    for pattern in nicovideo_patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    
    # Dailymotion
    dailymotion_patterns = [
        r'dailymotion\.com/video/([^/?&]+)',
    ]
    
    for pattern in dailymotion_patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    
    return None
def validate_video_url(url: str) -> Tuple[bool, str]:
    """验证视频URL的安全性和合法性"""
    # 检查是否为HTTP/HTTPS URL
    if not url.startswith(('http://', 'https://')):
        return False, 'URL必须使用HTTP或HTTPS协议'
    
    # 检查是否为公网地址
    if not is_public_http_url(url):
        return False, 'URL不是公网地址'
    
    # 检查是否属于支持的平台
    is_supported, platform = is_video_platform(url)
    if not is_supported:
        return False, f'不支持的视频平台: {platform or "未知"}'
    
    # 检查URL格式
    from urllib.parse import urlparse
    parsed = urlparse(url)
    if not parsed.netloc or not parsed.path:
        return False, 'URL格式不正确'
    
    return True, 'URL验证通过'
# ============================================================
# 视频代理核心函数
# ============================================================

async def _fetch_video_content(session: aiohttp.ClientSession, url: str, 
                              headers: Dict = None) -> Tuple[Optional[bytes], Optional[str], Dict]:
    """异步获取视频内容"""
    try:
        default_headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Accept': 'video/*,application/octet-stream',
            'Referer': 'https://www.nanyi-anime-club.top/',
        }
        
        if headers:
            default_headers.update(headers)
        
        async with session.get(url, headers=default_headers, timeout=aiohttp.ClientTimeout(total=30)) as response:
            if response.status != 200:
                return None, f'HTTP状态码: {response.status}', response.headers
            
            content = await response.read()
            content_type = response.headers.get('Content-Type', '').lower()
            
            # 检查内容类型
            if not content_type.startswith('video/'):
                # 可能是HTML页面，提取视频信息
                content_text = content.decode('utf-8', errors='ignore')
                if 'video' in content_type or 'm3u8' in content_type:
                    return content, f'找到视频内容: {content_type}', response.headers
                else:
                    return None, f'内容类型不是视频: {content_type}', response.headers
            
            return content, content_type, response.headers
            
    except aiohttp.ClientError as e:
        return None, f'网络请求错误: {str(e)}', {}
    except Exception as e:
        return None, f'未知错误: {str(e)}', {}
async def _process_video_content(content: bytes, content_type: str) -> Tuple[bytes, str]:
    """处理视频内容（移除DRM、转换格式等）"""
    # 这里可以添加视频处理逻辑
    # 目前只是返回原始内容
    return content, content_type
def proxy_video(url: str, headers: Dict = None, ttl: int = None) -> Tuple[bool, Any, str]:
    """代理视频URL，获取视频内容"""
    try:
        # 验证URL
        is_valid, error_msg = validate_video_url(url)
        if not is_valid:
            return False, None, error_msg
        
        # 生成缓存键
        cache_key = generate_cache_key(url, headers)
        ttl = ttl or VIDEO_PROXY_CACHE_TTL
        
        # 检查缓存
        cached = get_cached_video(cache_key)
        if cached:
            logger.info(f'视频代理缓存命中: {cache_key}')
            return True, cached, '缓存命中'
        
        # 获取视频内容
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        async def _proxy():
            async with aiohttp.ClientSession() as session:
                content, error, response_headers = await _fetch_video_content(session, url, headers)
                if not content:
                    return False, error, response_headers
                
                # 处理视频内容
                processed_content, processed_type = await _process_video_content(content, response_headers.get('Content-Type', ''))
                
                # 缓存结果
                cache_data = {
                    'url': url,
                    'content': processed_content,
                    'content_type': processed_type,
                    'headers': dict(response_headers),
                    'expires': datetime.now() + timedelta(seconds=ttl),
                    'platform': is_video_platform(url)[1],
                    'video_id': extract_video_id(url),
                }
                
                set_cached_video(cache_key, cache_data)
                
                return True, {
                    'content': processed_content,
                    'content_type': processed_type,
                    'url': url,
                    'platform': is_video_platform(url)[1],
                    'video_id': extract_video_id(url),
                    'headers': dict(response_headers),
                }, '代理成功'
        
        result = loop.run_until_complete(_proxy())
        loop.close()
        
        return result
        
    except Exception as e:
        logger.error(f'视频代理失败: {str(e)}')
        return False, None, str(e)
def download_video(url: str, output_path: str = None) -> Tuple[bool, str, Optional[str]]:
    """下载视频到本地文件"""
    try:
        # 验证URL
        is_valid, error_msg = validate_video_url(url)
        if not is_valid:
            return False, error_msg, None
        
        # 使用requests同步下载
        response = requests.get(url, stream=True, timeout=30)
        if response.status_code != 200:
            return False, f'下载失败，HTTP状态码: {response.status_code}', None
        
        # 获取文件扩展名
        from urllib.parse import urlparse
        parsed = urlparse(url)
        path = parsed.path
        ext = path.split('.')[-1] if '.' in path else 'mp4'
        
        # 如果未指定输出路径，使用默认路径
        if not output_path:
            output_path = f'video_{extract_video_id(url) or hashlib.md5(url.encode()).hexdigest()[:8]}.{ext}'
        
        # 写入文件
        with open(output_path, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)
        
        logger.info(f'视频下载成功: {output_path}')
        return True, '下载成功', output_path
        
    except Exception as e:
        logger.error(f'视频下载失败: {str(e)}')
        return False, str(e), None
def process_video(url: str, operations: List[str] = None) -> Tuple[bool, Any, str]:
    """处理视频（裁剪、转码、添加水印等）"""
    try:
        # 获取视频内容
        success, video_data, error = proxy_video(url)
        if not success:
            return False, None, f'获取视频失败: {error}'
        
        content = video_data['content']
        content_type = video_data['content_type']
        
        # 根据操作进行处理
        if not operations:
            return True, {
                'content': content,
                'content_type': content_type,
                'url': url,
                'operations': [],
            }, '处理完成'
        
        # 这里可以添加视频处理逻辑
        # 目前先返回原始内容
        return True, {
            'content': content,
            'content_type': content_type,
            'url': url,
            'operations': operations,
        }, '处理完成'
        
    except Exception as e:
        logger.error(f'视频处理失败: {str(e)}')
        return False, None, str(e)
# ============================================================
# 主机管理
# ============================================================

def add_video_host(host: str, is_trusted: bool = False, description: str = "") -> Tuple[bool, str]:
    """添加视频代理主机到白名单"""
    try:
        # 检查是否已存在
        existing = VideoHost.query.filter_by(host=host).first()
        if existing:
            return False, f'主机 {host} 已存在'
        
        # 创建新主机记录
        video_host = VideoHost(
            host=host,
            is_trusted=is_trusted,
            description=description,
            added_at=datetime.now(),
        )
        
        db.session.add(video_host)
        db.session.commit()
        
        logger.info(f'添加视频主机成功: {host}')
        return True, '添加成功'
        
    except Exception as e:
        db.session.rollback()
        logger.error(f'添加视频主机失败: {str(e)}')
        return False, str(e)
def remove_video_host(host: str) -> Tuple[bool, str]:
    """从白名单中移除视频主机"""
    try:
        video_host = VideoHost.query.filter_by(host=host).first()
        if not video_host:
            return False, f'主机 {host} 不存在'
        
        db.session.delete(video_host)
        db.session.commit()
        
        logger.info(f'移除视频主机成功: {host}')
        return True, '移除成功'
        
    except Exception as e:
        db.session.rollback()
        logger.error(f'移除视频主机失败: {str(e)}')
        return False, str(e)
def get_video_hosts() -> List[Dict]:
    """获取所有视频主机白名单"""
    try:
        hosts = VideoHost.query.order_by(VideoHost.added_at.desc()).all()
        
        return [{
            'id': host.id,
            'host': host.host,
            'is_trusted': host.is_trusted,
            'description': host.description,
            'added_at': host.added_at.isoformat() if host.added_at else None,
            'request_count': host.request_count,
            'success_count': host.success_count,
        } for host in hosts]
        
    except Exception as e:
        logger.error(f'获取视频主机失败: {str(e)}')
        return []
# ============================================================
# 缓存管理
# ============================================================

def get_cached_video(cache_key: str) -> Optional[Dict]:
    """获取缓存的视频数据"""
    try:
        cache_record = VideoCache.query.filter_by(cache_key=cache_key).first()
        if not cache_record:
            return None
        
        # 检查是否过期
        if cache_record.expires_at and cache_record.expires_at < datetime.now():
            db.session.delete(cache_record)
            db.session.commit()
            return None
        
        # 更新访问计数
        cache_record.access_count += 1
        db.session.commit()
        
        # 返回解密的数据
        return cache_record.data
        
    except Exception as e:
        logger.error(f'获取视频缓存失败: {str(e)}')
        return None
def set_cached_video(cache_key: str, data: Dict) -> bool:
    """设置视频缓存"""
    try:
        # 先删除旧缓存
        VideoCache.query.filter_by(cache_key=cache_key).delete()
        
        # 创建新缓存记录
        cache_record = VideoCache(
            cache_key=cache_key,
            data=data,
            expires_at=datetime.now() + timedelta(seconds=VIDEO_PROXY_CACHE_TTL),
            platform=data.get('platform'),
            video_id=data.get('video_id'),
        )
        
        db.session.add(cache_record)
        db.session.commit()
        
        logger.info(f'设置视频缓存成功: {cache_key}')
        return True
        
    except Exception as e:
        db.session.rollback()
        logger.error(f'设置视频缓存失败: {str(e)}')
        return False
def clear_video_cache() -> bool:
    """清空视频缓存"""
    try:
        VideoCache.query.delete()
        db.session.commit()
        
        logger.info('清空视频缓存成功')
        return True
        
    except Exception as e:
        db.session.rollback()
        logger.error(f'清空视频缓存失败: {str(e)}')
        return False
# ============================================================
# Flask API 路由
# ============================================================

def register_video_proxy_routes(app):
    """注册视频代理相关路由"""
    
    @app.route('/api/video/proxy', methods=['POST'])
    def api_proxy_video():
        """代理视频URL"""
        from .utils import get_or_404
        
        data = request.get_json() or {}
        url = data.get('url')
        if not url:
            return jsonify({'error': 'URL参数缺失'}), 400
        
        headers = data.get('headers', {})
        ttl = data.get('ttl')
        
        success, video_data, error = proxy_video(url, headers, ttl)
        if not success:
            return jsonify({'error': error}), 500
        
        return jsonify({
            'success': True,
            'video': {
                'url': video_data['url'],
                'content_type': video_data['content_type'],
                'platform': video_data['platform'],
                'video_id': video_data['video_id'],
                'size': len(video_data['content']) if video_data['content'] else 0,
                'headers': video_data['headers'],
            }
        })
    
    @app.route('/api/video/download', methods=['POST'])
    def api_download_video():
        """下载视频到本地"""
        from .utils import get_or_404
        
        data = request.get_json() or {}
        url = data.get('url')
        output_path = data.get('output_path')
        
        if not url:
            return jsonify({'error': 'URL参数缺失'}), 400
        
        success, error, file_path = download_video(url, output_path)
        if not success:
            return jsonify({'error': error}), 500
        
        return jsonify({
            'success': True,
            'file_path': file_path,
            'url': url,
        })
    
    @app.route('/api/video/process', methods=['POST'])
    def api_process_video():
        """处理视频"""
        from .utils import get_or_404
        
        data = request.get_json() or {}
        url = data.get('url')
        operations = data.get('operations', [])
        
        if not url:
            return jsonify({'error': 'URL参数缺失'}), 400
        
        success, video_data, error = process_video(url, operations)
        if not success:
            return jsonify({'error': error}), 500
        
        return jsonify({
            'success': True,
            'video': {
                'url': video_data['url'],
                'content_type': video_data['content_type'],
                'operations': video_data['operations'],
                'size': len(video_data['content']) if video_data['content'] else 0,
            }
        })
    
    @app.route('/api/video/hosts', methods=['GET'])
    def api_get_video_hosts():
        """获取视频主机白名单"""
        hosts = get_video_hosts()
        return jsonify({
            'success': True,
            'hosts': hosts,
        })
    
    @app.route('/api/video/hosts', methods=['POST'])
    def api_add_video_host():
        """添加视频主机"""
        from .utils import get_or_404
        
        data = request.get_json() or {}
        host = data.get('host')
        is_trusted = data.get('is_trusted', False)
        description = data.get('description', '')
        
        if not host:
            return jsonify({'error': '主机参数缺失'}), 400
        
        success, error = add_video_host(host, is_trusted, description)
        if not success:
            return jsonify({'error': error}), 500
        
        return jsonify({
            'success': True,
            'message': '添加成功',
        })
    
    @app.route('/api/video/hosts/<host>', methods=['DELETE'])
    def api_remove_video_host(host):
        """移除视频主机"""
        success, error = remove_video_host(host)
        if not success:
            return jsonify({'error': error}), 500
        
        return jsonify({
            'success': True,
            'message': '移除成功',
        })
    
    @app.route('/api/video/cache/clear', methods=['POST'])
    def api_clear_video_cache():
        """清空视频缓存"""
        success = clear_video_cache()
        if not success:
            return jsonify({'error': '清空缓存失败'}), 500
        
        return jsonify({
            'success': True,
            'message': '清空缓存成功',
        })
    
    @app.route('/api/video/validate', methods=['POST'])
    def api_validate_video_url():
        """验证视频URL"""
        from .utils import get_or_404
        
        data = request.get_json() or {}
        url = data.get('url')
        
        if not url:
            return jsonify({'error': 'URL参数缺失'}), 400
        
        is_valid, error_msg = validate_video_url(url)
        if not is_valid:
            return jsonify({'success': False, 'error': error_msg}), 400
        
        return jsonify({
            'success': True,
            'message': 'URL验证通过',
        })
    
    @app.route('/api/video/info', methods=['POST'])
    def api_get_video_info():
        """获取视频信息"""
        from .utils import get_or_404
        
        data = request.get_json() or {}
        url = data.get('url')
        
        if not url:
            return jsonify({'error': 'URL参数缺失'}), 400
        
        is_valid, platform = is_video_platform(url)
        if not is_valid:
            return jsonify({'error': '不支持的视频平台'}), 400
        
        video_id = extract_video_id(url)
        
        return jsonify({
            'success': True,
            'video_info': {
                'url': url,
                'platform': platform,
                'video_id': video_id,
                'host': VideoHost.query.filter_by(host=url.split('/')[2]).first() is not None,
            }
        })

# ============================================================
# 后台任务和统计
# ============================================================

def update_host_stats(host: str, success: bool):
    """更新主机统计信息"""
    try:
        video_host = VideoHost.query.filter_by(host=host).first()
        if video_host:
            video_host.request_count += 1
            if success:
                video_host.success_count += 1
            db.session.commit()
            
    except Exception as e:
        logger.error(f'更新主机统计失败: {str(e)}')
def get_video_statistics() -> Dict:
    """获取视频代理统计信息"""
    try:
        total_proxies = VideoCache.query.count()
        total_hosts = VideoHost.query.count()
        
        # 缓存命中率
        total_requests = db.session.query(func.sum(VideoCache.access_count)).scalar() or 0
        successful_requests = db.session.query(
            func.sum(VideoCache.access_count)
        ).filter(VideoCache.access_count > 0).scalar() or 0
        
        hit_rate = (successful_requests / total_requests * 100) if total_requests > 0 else 0
        
        return {
            'total_proxies': total_proxies,
            'total_hosts': total_hosts,
            'cache_hit_rate': hit_rate,
            'cache_size': len(VideoCache.query.all()),
        }
        
    except Exception as e:
        logger.error(f'获取视频统计失败: {str(e)}')
        return {}
# ============================================================
# 初始化函数
# ============================================================

def init_video_proxy():
    """初始化视频代理系统"""
    try:
        # 创建默认主机白名单
        default_hosts = [
            'youtube.com',
            'www.youtube.com',
            'bilibili.com',
            'www.bilibili.com',
            'nicovideo.jp',
            'www.nicovideo.jp',
            'dailymotion.com',
            'www.dailymotion.com',
            'twitch.tv',
            'www.twitch.tv',
        ]
        
        for host in default_hosts:
            existing = VideoHost.query.filter_by(host=host).first()
            if not existing:
                video_host = VideoHost(
                    host=host,
                    is_trusted=True,
                    description='默认白名单主机',
                    added_at=datetime.now(),
                )
                db.session.add(video_host)
        
        db.session.commit()
        logger.info('视频代理系统初始化完成')
        
    except Exception as e:
        db.session.rollback()
        logger.error(f'视频代理系统初始化失败: {str(e)}')