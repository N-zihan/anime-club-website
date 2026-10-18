"""
动漫社官网 · 工具函数模块
====================================

本模块提供全局工具函数和第三方服务客户端初始化：

1. Supabase 客户端：
   - 从环境变量读取 URL 和密钥
   - 用于照片文件的上传、删除、获取公开链接

2. 文件类型校验：
   - allowed_file() 函数检查文件扩展名
   - 仅允许 png, jpg, jpeg, gif 四种格式
   - 用于头像上传和照片上传

本模块是整个项目中唯一与 Supabase SDK 直接交互的地方。
"""

import hashlib
import hmac
import io
import ipaddress
import os
import socket
import time
import uuid
import re
from urllib.parse import urljoin, urlparse

import requests
from PIL import Image
from dotenv import load_dotenv
from flask import abort, current_app
from supabase import create_client, Client

from .models import db

load_dotenv()

SUPABASE_URL = os.getenv('SUPABASE_URL')
SUPABASE_KEY = os.getenv('SUPABASE_KEY')

_supabase_client: Client | None = None # pylint: disable=invalid-name

# 显式拉黑的特殊网段：标准库的 is_private/is_reserved 覆盖不到它们，
# 但都是真实可路由的内网/元数据地址，必须拒绝。
_BLOCKED_NETWORKS = tuple(ipaddress.ip_network(cidr) for cidr in (
    '100.64.0.0/10',    # RFC 6598 CGNAT（云内网常用）
    '100.100.100.200/32',  # 阿里云 metadata 服务
    '192.0.0.0/24',     # IETF 协议专用
    '198.18.0.0/15',    # 基准测试网段
))


def get_supabase() -> Client:
    """懒加载获取 Supabase 客户端，仅在首次调用时初始化"""
    global _supabase_client # pylint: disable=global-statement
    if _supabase_client is None:
        # 测试环境返回 Mock 客户端
        if os.getenv('TESTING') == '1':
            from unittest.mock import MagicMock # pylint: disable=import-outside-toplevel
            _supabase_client = MagicMock()
            return _supabase_client
        if not SUPABASE_URL or not SUPABASE_KEY:
            raise RuntimeError("Supabase 未配置：请检查 SUPABASE_URL 和 SUPABASE_KEY 环境变量")
        _supabase_client = create_client(SUPABASE_URL, SUPABASE_KEY)
    return _supabase_client


# ============================================================
# SSRF 防护：只允许访问公网的 http(s) 地址
# ============================================================

def _is_blocked_ip(ip):
    """单个 IP 是否属于禁止访问的范围。

    Python 3.14 的 is_private / is_reserved 覆盖不到下面这几段，实测
    `http://100.100.100.200/`（阿里云 metadata）与 `http://100.64.0.1/`（CGNAT）
    都被判成"公网"放行，等于把云上实例的凭证接口暴露给匿名请求。
    """
    if (ip.is_private or ip.is_loopback or ip.is_link_local
            or ip.is_reserved or ip.is_multicast or ip.is_unspecified):
        return True
    # is_global 是标准库给出的"可全球路由"判据，用它兜住上面枚举漏掉的段
    # （0.0.0.0/8、198.18.0.0/15 等特殊用途地址都在其中）。
    if not ip.is_global:
        return True
    for network in _BLOCKED_NETWORKS:
        if ip in network:
            return True
    return False


def resolve_public_ip(host, port):
    """把主机名解析为「一个可信的公网 IP」；不可信或解析失败时返回 None。

    关键点：只在这里解析一次，把结果交给调用方；避免"校验时解析一次、
    建连时再解析一次"造成 DNS rebinding（TOCTOU）窗口 —— 攻击者可以让域名
    第一次返回公网 IP 骗过校验，第二次解析到 127.0.0.1。
    """
    if not host:
        return None
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except (socket.gaierror, UnicodeError, OSError):
        return None
    if not infos:
        return None
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            return None
        if _is_blocked_ip(ip):
            return None
    return infos[0][4][0]


def _parse_http_url(url):
    """解析出 (host, port)；URL 非法时返回 None"""
    try:
        parsed = urlparse(url)
    except (ValueError, AttributeError):
        return None
    if parsed.scheme not in ('http', 'https'):
        return None
    host = parsed.hostname
    if not host:
        return None
    try:
        port = parsed.port or (443 if parsed.scheme == 'https' else 80)
    except ValueError:
        return None
    return host, port


def is_public_http_url(url):
    """校验 URL 是否指向公网 http(s) 地址。

    阻止内网 / 环回 / 链路本地（含云元数据 169.254.169.254）/ 保留地址，
    避免服务端被当成跳板去访问内网。
    """
    parsed = _parse_http_url(url)
    if parsed is None:
        return False
    host, port = parsed
    return resolve_public_ip(host, port) is not None


def _peer_is_public(response, expected_port):
    """复核实际建连的对端 IP，作为 DNS rebinding 的第二道防线。

    返回 (ok, local_redirect_ok)：
      ok=False → 必须整体失败；
      local_redirect_ok=False → 对端是本地代理，跳转不能由本地跟随。
    """
    try:
        connection = getattr(response.raw, '_connection', None)
        peer = connection.sock.getpeername() if connection is not None and connection.sock else None
    except Exception:  # pylint: disable=broad-except
        peer = None
    if not peer:
        # 拿不到对端（urllib3 可能已释放连接）：退回逐跳校验，不假设通过
        return True, True
    try:
        peer_ip = ipaddress.ip_address(peer[0])
        peer_port = peer[1]
    except (ValueError, IndexError, TypeError):
        return False, True
    if not _is_blocked_ip(peer_ip):
        return True, True
    # 目标是公网、对端却是内网：唯一合理解释是走了本地代理（开发机/CI 常见，
    # 例如 127.0.0.1:7890）。此时禁止由本地跟随重定向（否则重定向目标绕过了
    # 逐跳校验），响应本身仍交回调用方。
    if peer_ip.is_loopback and peer_port != expected_port:
        return True, False
    return False, True


def safe_get(url, max_redirects=4, **kwargs):
    """带 SSRF 防护的 requests.get：逐跳校验目标地址。

    返回 requests.Response；地址不合法或重定向过多时返回 None。
    重定向一律由本函数自己跟随（allow_redirects=False），每一跳都重新校验，
    并在拿到响应头后复核真实对端 IP。
    """
    kwargs.pop('allow_redirects', None)
    kwargs.setdefault('stream', True)  # 先只取响应头，复核对端后再读 body
    for _ in range(max_redirects + 1):
        parsed = _parse_http_url(url)
        if parsed is None:
            return None
        host, port = parsed
        if resolve_public_ip(host, port) is None:
            return None
        try:
            res = requests.get(url, allow_redirects=False, **kwargs)
        except requests.RequestException:
            return None
        ok, local_redirect_ok = _peer_is_public(res, port)
        if not ok:
            res.close()
            return None
        if res.is_redirect and res.headers.get('Location') and local_redirect_ok:
            url = urljoin(url, res.headers['Location'])
            res.close()
            continue
        return res
    return None


# ============================================================
# 邮箱验证码：session 是「签名但未加密」的 cookie，不能存明文
# ============================================================

def code_digest(code):
    """验证码摘要。客户端能自行解出 session 里的明文，故只存摘要。"""
    return hashlib.sha256(f'{current_app.secret_key}:{code}'.encode()).hexdigest()


def code_matches(stored_digest, code):
    if not stored_digest or not code:
        return False
    return hmac.compare_digest(stored_digest, code_digest(code))


_ACTION_THROTTLE = {}


def throttle_ok(key, seconds):
    """进程内简易限流，用于挡住最粗暴的邮件轰炸/验证码枚举循环。

    注意：Serverless 多实例下不是强保证，生产建议再叠加 Flask-Limiter
    或平台级（WAF）限流。
    """
    now = time.monotonic()
    last = _ACTION_THROTTLE.get(key)
    if last is not None and now - last < seconds:
        return False
    if len(_ACTION_THROTTLE) > 5000:  # 防止自身无上限增长
        _ACTION_THROTTLE.clear()
    _ACTION_THROTTLE[key] = now
    return True


# 登录失败计数器：window 秒内连续失败 max_failures 次才拦截，
# 只计失败、成功即清零，避免正常用户输错一次密码就被锁 30 秒。
_LOGIN_FAILS = {}


def login_fail_hit_limit(key, max_failures=5, window=60):
    """记录一次登录失败；该 key 在 window 秒内失败超过 max_failures 次时返回 True。"""
    now = time.monotonic()
    count, start = _LOGIN_FAILS.get(key, (0, now))
    if now - start > window:
        count, start = 0, now
    count += 1
    if len(_LOGIN_FAILS) > 5000:  # 防止自身无上限增长
        _LOGIN_FAILS.clear()
    _LOGIN_FAILS[key] = (count, start)
    return count > max_failures


def login_fail_is_limited(key, max_failures=5, window=60):
    """该 key 在 window 秒内失败次数是否已超过 max_failures（不计数，只查询）。"""
    now = time.monotonic()
    entry = _LOGIN_FAILS.get(key)
    if entry is None:
        return False
    count, start = entry
    if now - start > window:
        return False
    return count > max_failures


def login_fail_reset(key):
    """登录成功后清除失败计数。"""
    _LOGIN_FAILS.pop(key, None)


ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def compress_image(file_data, max_size=(800, 800), quality=85, output_format='JPEG'):
    """
    压缩图片数据
    :param file_data: 原始图片二进制数据
    :param max_size: 最大宽高 (width, height)
    :param quality: JPEG品质 1-100
    :param output_format: 'JPEG' 或 'PNG'
    :return: 压缩后的二进制数据
    """
    img = Image.open(io.BytesIO(file_data))

    # 处理透明背景（PNG转RGB）
    if img.mode in ('RGBA', 'LA'):
        background = Image.new('RGB', img.size, (255, 255, 255))
        background.paste(img, mask=img.split()[-1])
        img = background
    elif img.mode != 'RGB':
        img = img.convert('RGB')

    # 缩放到指定尺寸（保持宽高比）
    img.thumbnail(max_size, Image.Resampling.LANCZOS)

    output = io.BytesIO()
    img.save(output, format=output_format, quality=quality, optimize=True)
    return output.getvalue()


def get_or_404(model, ident):
    obj = db.session.get(model, ident)
    if obj is None:
        abort(404)
    return obj


def upload_avatar_to_storage(user_id, image_data):
    """上传头像到 Supabase Storage，返回公开 URL"""
    supabase = get_supabase()
    filename = f"user_{user_id}_{uuid.uuid4().hex[:8]}.jpg"
    
    # 文件类型验证
    if not allowed_file(filename):
        raise ValueError("不支持的文件类型")
    
    # 文件大小验证
    max_size = int(os.getenv('AVATAR_MAX_SIZE', 2 * 1024 * 1024))  # 默认 2MB
    if len(image_data) > max_size:
        raise ValueError(f"头像文件大小不能超过 {max_size} 字节")
    
    # 文件头验证
    if not image_data.startswith(b'\xff\xd8\xff'):
        raise ValueError("不支持的图片格式，仅支持 JPEG 图片")
    
    # 图片压缩
    try:
        compressed_data = compress_image(image_data, max_size=(800, 800), quality=85)
    except Exception as e:
        raise ValueError(f"图片压缩失败: {str(e)}")
    
    # 上传到 Supabase Storage
    supabase.storage.from_('avatars').upload(
        filename,
        compressed_data,
        file_options={"content-type": "image/jpeg"}
    )
    return supabase.storage.from_('avatars').get_public_url(filename)
    
    
# 文件安全验证工具
def _secure_filename(filename):
    """安全的文件名处理"""
    import os
    
    # 防止路径遍历攻击
    filename = os.path.basename(filename)
    
    # 移除或替换不安全字符
    filename = re.sub(r'[^\w\-_.]', '_', filename)
    
    # 移除开头和结尾的点，避免隐藏文件
    filename = filename.strip('.')
    
    # 防止控制字符
    filename = re.sub(r'[\x00-\x1f\x7f-\x9f]', '', filename)
    
    # 限制文件名长度
    if len(filename) > 255:
        name, ext = os.path.splitext(filename)
        filename = name[:255 - len(ext)] + ext
    
    return filename


def _validate_file_content(file_data, filename):
    """验证文件内容和类型的完整验证"""
    import magic
    import imghdr
    
    # 获取文件头信息
    file_header = file_data[:1024]  # 读取前1KB作为文件头
    
    # 使用python-magic检查MIME类型
    try:
        mime = magic.Magic(mime=True)
        mime_type = mime.from_buffer(file_header)
    except:
        # 如果magic不可用，回退到图片头检查
        mime_type = imghdr.what(None, h=file_header) or 'unknown'
    
    # 扩展名与MIME类型验证
    ext = filename.rsplit('.', 1)[1].lower() if '.' in filename else ''
    
    # 图片类型验证
    image_mimes = ['image/jpeg', 'image/jpg', 'image/png', 'image/gif']
    image_exts = ['jpg', 'jpeg', 'png', 'gif']
    
    if mime_type in image_mimes or ext in image_exts:
        # 图片类型验证
        if mime_type not in image_mimes:
            raise ValueError(f"文件类型不匹配：扩展名 .{ext} 与MIME类型 {mime_type} 不符")
        
        # 图片内容验证
        if mime_type == 'image/jpeg':
            if not file_header.startswith(b'\xff\xd8\xff'):
                raise ValueError("JPEG文件头验证失败")
        elif mime_type == 'image/png':
            if not file_header.startswith(b'\x89PNG\r\n\x1a\n'):
                raise ValueError("PNG文件头验证失败")
        elif mime_type == 'image/gif':
            if not (file_header.startswith(b'GIF87a') or file_header.startswith(b'GIF89a')):
                raise ValueError("GIF文件头验证失败")
                
    elif mime_type == 'application/octet-stream':
        # 无法识别的二进制文件，检查扩展名
        if ext not in image_exts:
            raise ValueError(f"不允许的扩展名 .{ext}")
    else:
        raise ValueError(f"不支持的文件类型：{mime_type}")
    
    return True


def validate_file_upload(file_data, filename, max_size_mb=5):
    """完整的文件上传安全验证
    
    Args:
        file_data: 文件二进制数据
        filename: 原始文件名
        max_size_mb: 文件最大大小(MB)
        
    Returns:
        dict: 验证结果，包含是否通过和错误信息
    """
    errors = []
    
    # 1. 文件名安全验证
    try:
        secure_filename = _secure_filename(filename)
        if not secure_filename or secure_filename != filename:
            errors.append("文件名安全验证失败")
    except Exception as e:
        errors.append(f"文件名安全验证异常：{str(e)}")
    
    # 2. 文件大小验证
    try:
        max_size = max_size_mb * 1024 * 1024
        if len(file_data) > max_size:
            errors.append(f"文件大小超过限制：{len(file_data)} 字节 > {max_size} 字节")
    except Exception as e:
        errors.append(f"文件大小验证异常：{str(e)}")
    
    # 3. 文件内容和类型验证
    try:
        _validate_file_content(file_data, secure_filename)
    except ValueError as e:
        errors.append(f"文件内容验证失败：{str(e)}")
    except Exception as e:
        errors.append(f"文件内容验证异常：{str(e)}")
    
    # 4. 防止路径遍历攻击
    try:
        if '..' in filename or filename.startswith('/'):
            errors.append("检测到路径遍历攻击")
        
        # 检查空文件
        if len(file_data) == 0:
            errors.append("文件不能为空")
    except Exception as e:
        errors.append(f"路径遍历攻击验证异常：{str(e)}")
    
    return {
        'is_valid': len(errors) == 0,
        'errors': errors,
        'secure_filename': secure_filename if not errors else None
    }
