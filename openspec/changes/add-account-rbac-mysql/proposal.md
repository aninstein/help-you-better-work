# 注册登录、Hinter RBAC 与 MySQL 部署

## Why

当前系统允许浏览器选择 `talker` / `hinter` Demo 身份，账户、权限和资源所有权没有形成真实登录边界。SQLite 与 Markdown 文件也不能作为新的多用户个人员工服务的最终存储方案。

2026-10-25 用户要求手机、邮箱、密码注册，首期密码登录；默认提供名为 Hinter Admin 的管理员，并由管理员授予用户 Hinter 权限；存储迁入独立 MySQL Docker 服务，以 Docker Compose 管理和调整部署脚本。

## What Changes

- **BREAKING**：统一账户注册和密码登录，移除通过 `/auth/demo` 或 URL 参数获得身份/权限的入口。
- 首期普通注册填写显示名、手机号、邮箱、密码，使用手机号或邮箱加密码登录；短信验证码、邮件验证码和第三方登录留待后续。
- 使用 `talker` 与 `hinter_admin` 两个角色；公开注册默认只有 `talker`，Hinter 权限必须由有权限的管理员授予。
- 幂等初始化显示名为 **Hinter Admin** 的管理员账户，生成部署独有的高强度随机初始密码，首次登录后修改密码。
- 登录后进入个人 Agent 聊天页，右上角显示账号与角色状态；管理员可进入 Hinter 工作台和用户授权管理。
- 将账户、RBAC、登录会话、聊天记录、Agent Run、事件、Wiki/记忆及 DeerFlow checkpoint/Store 迁入 MySQL。
- 增加独立 `mysql` 容器、迁移和初始化任务、Agent worker、持久卷、健康依赖；更新默认与外部模型两种 Compose 启动路径。
- 提供旧 SQLite/Markdown 导入、旧 Talker 归属映射、备份恢复和部署后的完整登录/授权/持久化验收。

## Capabilities

### New Capabilities

- `account-access-control`：统一注册、密码认证、会话、Hinter 管理授权和登录态页面。
- `mysql-storage-deployment`：权威 MySQL 存储、DeerFlow 适配、迁移与 Compose 部署。

### Modified Capabilities

无已归档 capability 的增量修改。`manage-talker-workspaces` 的 Demo 身份与任意创建 Talker 行为由本 change 的真实账户/所有权规则取代。

## Impact

- Backend：认证中间件、所有 REST/WS/音频接口、Store/Wiki repository、数据库迁移、账户/RBAC API。
- Frontend：登录/注册页、受保护聊天入口、右上角登录态、Hinter 用户与权限管理。
- Deploy：`compose.yaml`、`compose.external.yaml`、`Dockerfile`、启动/停止/entrypoint 脚本、环境配置和运维文档。
- Data：本地 Demo 身份不直接变成已验证真实账户；旧资料通过显式归属映射导入。

## Dependencies and Scope

- 与 [`adopt-deerflow-personal-agent`](../adopt-deerflow-personal-agent/proposal.md) 共用用户、thread、Run 与记忆所有权。
- 首期是单应用 RBAC，不新增组织/企业多租户层级或复杂审批流。
- 已完成 MySQL 容器、管理员 bootstrap、账户权限和数据源切换，见 implementation-status.md。
