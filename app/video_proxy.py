"""
视频代理层【当前未启用 · NOT ENABLED】
====================================

状态说明：
  - 本模块**不会注册任何路由**（保持历史行为：该能力从未真正启用）。
  - 只要 `register_video_proxy_routes` 存在，`app/__init__.py` 的可选导入就会
    成功，不会再在启动时打印“缺少 aiohttp”的误导性警告。
  - 本模块只依赖 requests（requirements.txt 已有），不使用 aiohttp。

【为什么不做“破解式”代理】
  - 绕过 DRM、破解会员 / 付费墙、规避地理限制，违反平台服务条款，
    在多数司法辖区还可能构成侵权或违法，本项目不提供此类能力。
  - 服务端代理也无法解决两类问题：
      1) 域名被网络封锁：服务端与用户网络环境不同，代理不改变可达性；
      2) YouTube 等使用 DASH + 签名分片：代理单个分片会因签名 / 时效失效。

【如需启用合规代理】
  仅针对**公开可嵌入**的视频资源做原样透传（白名单主机 + SSR 安全检查 +
  Range 流式响应），可在此文件中实现并在 `app/__init__.py` 注册路由。
"""


def register_video_proxy_routes(app):
    """可选模块占位：当前不注册任何路由，返回未启用的原因。

    :param app: Flask 应用实例（此处不使用）
    :return: 未启用原因说明
    """
    return 'video proxy disabled by design (see module docstring)'
