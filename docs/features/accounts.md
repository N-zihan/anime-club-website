# 账号、验证码与权限

## 1. 注册流程

社团站**不对外开放注册**：注册时必须填对社团群验证码（`GROUP_VERIFICATION_CODE`），这个码只在社团群里公开。

表单字段：

| 字段 | 校验 |
|---|---|
| `username` | 必填、唯一 |
| `qq` | 必填 |
| `email` | 必填、唯一，用于收验证码/找回密码 |
| `code` | 6 位数字邮箱验证码 |
| `group` | 社团群验证码 |
| `password` | 最少 6 位 |

```text
提交注册
  ├─ 群验证码错误 → 400 + 提示，不建号
  ├─ 邮箱验证码错误/过期 → 400
  ├─ 用户名/邮箱重复 → 400
  └─ 全部通过 → 建用户 → 自动登录 → 跳首页
```

![登录页](../screenshots/login.webp)

## 2. 登录与会话

- 密码用 Werkzeug 的 `generate_password_hash` / `check_password_hash` 存取，**数据库里没有明文**。
- 登录成功后 session 里记 `user_id` / `username` / `user_role`。
- 会话 Cookie 的 `Secure` 标志跟随部署平台：Vercel 默认开，本地默认关，可用 `SESSION_COOKIE_SECURE` 显式覆盖。

## 3. 访客拦截（全局守卫）

`app/__init__.py` 里有一个 `before_request` 守卫：**没登录时，只有白名单路由能访问**，其余一律 302 到 `/login`。

白名单（公开可访问）：

| 路由 | 说明 |
|---|---|
| `auth.*` | 登录、注册、找回密码、验证码 |
| `static` | 静态资源 |
| `public.splash` | 着陆页 |
| `public.anime_news` / `public.anime_news_article` | 番剧新闻（聚合资讯，无需登录） |
| 新闻相关 API | 来源列表、搜索、图片代理等 |
| `public.settings` | 站点设置（主题/语言是本机偏好，未登录也能调） |
| `public.manifest` / `robots` / `sitemap` / `service_worker` | PWA / 抓取入口（robots、sitemap 按请求域名动态生成） |
| `public.health` | 健康检查 `/api/health`（版本 + 数据库连通性，给监控/巡检用，不含敏感信息） |

其余（`/home`、`/board`、`/members`、`/about`、`/contest_center`、`/admin/*`…）全部要求登录。

!!! tip "新增页面时注意"
    新加的路由默认就是"登录可见"——这是安全默认值。如果确实需要公开（比如再做一个资讯页），必须**显式**加进白名单，并想清楚会不会泄露社员内容。

## 4. 找回密码

```text
输入注册邮箱
  → 生成一次性重置链接（带签名 token，限时有效）
  → 通过 SMTP 发出
  → 点链接设置新密码 → token 失效
```

SMTP 未配置时重置链接发不出去，找回密码不可用；**注册同理**——注册要邮箱验证码，
所以线上部署必须配 `MAIL_USERNAME` / `MAIL_PASSWORD`（见[平台部署](../deployment.md)）。
本地演示模式用预置账号 `testuser`，不需要注册，因此可以不配 SMTP。

## 5. 权限分级

| 角色 | 判定 | 能做什么 |
|---|---|---|
| 访客 | 未登录 | 着陆页、登录/注册、番剧新闻、站点设置（本机偏好） |
| 社员 | 已登录 | 论坛、照片墙、番剧资源、萌战投票/提名、通知、AI 好友、个人主页、设置 |
| 运营（staff） | `is_staff=True` | 社员权限 + `/staff/dashboard`（审核提名、处理举报等） |
| 站长（owner） | `is_owner=True` | 全部权限 + `/admin/*` 管理后台（用户管理、赛事管理、数据导出、AI 人设下架） |

- 一个人可以同时是 staff 和 owner（演示账号 `testuser` 就是）。
- 权限判断统一走装饰器（`admin_required` 等），**不要**在模板里写业务判断当权限用。
- 管理后台按钮在导航抽屉里按权限显示，但真正的拦截在后端；前端的隐藏只是体验优化，不构成安全边界。

## 6. 个人资料

![个人主页](../screenshots/profile.webp)

- 改用户名：唯一性校验，历史帖子/回复通过 `user_id` 外键保留。
- 改头像：上传 → 压缩 → 存 Supabase Storage 的 `avatars` 桶。
- **没上传头像时**：后端返回一枚"渐变底色 + 用户名首字"的 SVG 头像（按用户 id 轮换 6 套配色），
  而不是空白图——这是为了避免截图/列表里出现一堆灰色方块。
- 改密码：需要旧密码验证。
- `preferences` 字段（JSON 文本）存**账号级**偏好（通知免打扰等）；主题/字号/语言这类**本机偏好**存在 `localStorage`，不跟随账号。
