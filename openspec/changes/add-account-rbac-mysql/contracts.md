# 登录、RBAC、数据库与部署契约（目标）

## 1. 公开认证接口

| 接口 | 输入/输出要点 |
| --- | --- |
| `POST /api/v1/auth/register` | `phone`、`email`、`password`，可选 `display_name`；创建普通账户，成功 `201` |
| `POST /api/v1/auth/login` | `identifier`（手机/邮箱；bootstrap 可用别名）、`password`；成功设置会话 cookie |
| `GET /api/v1/auth/me` | 有效会话返回当前账户、角色、capabilities 和 CSRF 初始化信息；无会话 `401` |
| `POST /api/v1/auth/logout` | 撤销当前会话并清理 cookie；重复退出幂等 |
| `POST /api/v1/auth/change-password` | 登录态、旧密码、新密码；成功撤销旧会话并建立/要求新的登录态 |

注册样例中的值仅展示字段，不是默认账户或默认密码：

```json
{
  "display_name": "Example User",
  "phone": "+8613800000000",
  "email": "example@example.com",
  "password": "<user-chosen-password>"
}
```

成功登录或 `/auth/me` 的资料形态：

```json
{
  "user": {"id": "user_...", "display_name": "Example User"},
  "roles": ["talker"],
  "capabilities": ["chat.use", "records.read", "memory.manage"],
  "must_change_password": false,
  "default_workspace_id": "workspace_..."
}
```

capabilities 仍需结合资源所有权校验。注册请求包含 `roles`、`is_admin` 或他人 owner 字段时拒绝；注册成功不自动建立管理员会话。bootstrap 用户必须改密时仅允许资料、改密与退出等必要入口。

注册 `201` 返回 `user_id`、`default_workspace_id`、`roles=["talker"]` 和 `login_required=true`，页面随后引导密码登录；响应不返回密码、哈希或 bootstrap 凭据。

错误：`401 INVALID_CREDENTIALS`、`401 SESSION_EXPIRED`、`403 PERMISSION_DENIED`、`409 ACCOUNT_IDENTIFIER_EXISTS`、`409 LAST_ADMIN_REQUIRED`、`422 VALIDATION_ERROR`、`429 AUTH_RATE_LIMITED`。登录错误不泄露哪个账户标识存在。

## 2. 管理接口

| 接口 | 权限与行为 |
| --- | --- |
| `GET /api/v1/admin/users` | `users.read`；分页搜索用户及角色，不返回密码哈希 |
| `PUT /api/v1/admin/users/{id}/roles` | `roles.assign`；`roles`、`expected_version`，事务内授权并审计 |
| `PATCH /api/v1/admin/users/{id}/status` | `users.manage_status`；禁用/启用，禁用撤销会话与运行授权 |
| `GET /api/v1/admin/workspaces` | `workspace.supervise`；列出可监管个人空间 |
| `POST /api/v1/admin/legacy-workspace-bindings` | `legacy.bind`；显式把未认领旧空间映射到已注册账户，记录审计 |

角色修改请求：

```json
{
  "roles": ["talker", "hinter_admin"],
  "expected_version": 3
}
```

`talker` 是可正常使用产品的基础角色，`hinter_admin` 增加管理能力。Hinter 操作必须保留实际 actor；URL 中的显示模式不改变服务器身份。

## 3. 持久化边界

- MySQL 是用户、认证会话、权限、通用记录、Agent Run/event、记忆、checkpoint 和持久 Store 的权威来源。
- `workspace.owner_user_id` 从注册账户或显式迁移映射得出，不能从显示名猜测。
- `auth_sessions` 保存 token 摘要及失效信息；角色/账户版本变更在后续授权检查中生效。
- 同一 `(owner_user_id, operation, idempotency_key)` 只建立一次输入/Run。
- `(thread_id, seq)` 唯一；投影记录引用实际来源修订；worker 领取操作有 attempt/lease/fencing 信息。
- 文件路径必须在已校验 owner/thread 的根目录内，数据库可访问并不等于文件可以公开下载。

## 4. 部署配置

拟定环境契约，实施时统一收敛命名并同步示例：

| 配置 | 说明 |
| --- | --- |
| `DATABASE_URL` 或对应 secret file | MySQL 连接，正确处理特殊字符；日志不得输出凭据 |
| `MYSQL_DATABASE` / `MYSQL_USER` / 密码 secret | 独立数据库与应用账号；root 仅初始化使用 |
| `MYSQL_ROOT_PASSWORD_FILE` | MySQL 初始化管理密码文件 |
| `BOOTSTRAP_ADMIN_FILE` | 初次部署由操作者设置登录名和密码的本机 JSON 文件路径，不提交 |
| `BOOTSTRAP_ADMIN_PHONE` / `BOOTSTRAP_ADMIN_EMAIL` | 可选真实联系方式，不自动标为已验证 |
| `.runtime/bootstrap/bootstrap-admin.json` | 初次部署生成的私密配置，`0600` 且不提交 |
| `SESSION_COOKIE_SECURE` / `SESSION_TTL_SECONDS` | HTTPS Cookie 行为和会话期限 |
| `DEERFLOW_VERSION` / 配置文件 | 固定运行时版本/commit；名称不代表上游原生同名变量 |
| `DEER_FLOW_DATE_TIMEZONE` | 运行时日期上下文，默认 `Asia/Shanghai` |

这些变量中的私密值不得复制到 `.env.example`、代码、Prompt 或普通运行事件中。重复执行部署不更新已有管理员密码。

## 5. 运维与验收入口

- 升级流程提供 schema migration、bootstrap、legacy dry-run/import 和备份/恢复命令。
- `GET /healthz`：进程存活；目标新增 `GET /readyz`：schema、MySQL 与所需 provider 已就绪，失败为非成功状态。
- 启动脚本等待数据库、迁移、bootstrap、web/worker readiness；任一步失败退出非零并报告失败阶段。
- `docker-down.sh` 默认不带删除卷参数；恢复验证同时包含数据库和文件持久卷。
- 外部模型 override 必须保留数据库/迁移/bootstrap 依赖，不得照搬当前清空所有 `depends_on` 的行为。
