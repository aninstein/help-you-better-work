# 实施任务

已按批准规格实施。运行证据与验收范围见 [实施记录](implementation-status.md)。

## 1. 数据库与 DeerFlow provider
- [x] 1.1 固定 MySQL 8.4 LTS 已验证镜像版本，确定 driver、连接池、字符集与时区。
- [x] 1.2 建立账户/RBAC/session、记录、Run/event、记忆及迁移元数据 schema 和版本化 migrations。
- [x] 1.3 以固定 DeerFlow 版本实现 MySQL checkpointer、Store、应用 persistence 和记忆存储适配；禁用默认文件/SQLite 回退。
- [x] 1.4 验证数据库并发、幂等键、事件序列与任务租约，完成真实 Run/checkpoint 重启恢复。

## 2. 账户与初始化管理员
- [x] 2.1 实现手机/邮箱归一化与唯一注册、密码哈希、账号状态和统一错误码。
- [x] 2.2 实现手机或邮箱加密码登录、有状态 cookie 会话、CSRF、限流、退出和改密。
- [x] 2.3 实现幂等角色/权限 seed 与 Hinter Admin 账户初始化、随机初始密码文件和首次强制改密。
- [x] 2.4 保证再次部署不重置凭据或复活已撤销权限，验证最后管理员并发保护。

## 3. RBAC 与全接口授权
- [x] 3.1 增加用户搜索、角色授予/撤销、状态管理和授权审计 API。
- [x] 3.2 REST、WebSocket、SSE、音频、artifact 和 Wiki/记忆入口统一执行角色与所有权检查。
- [x] 3.3 在 DeerFlow lead agent、子任务和工具执行中贯通 actor/owner 与权限版本，处理撤权后的运行任务/连接。
- [x] 3.4 移除公开 Demo 身份签发和客户端选角色提权；旧导航参数仅用于受控路由。

## 4. 登录与管理页面
- [x] 4.1 实现登录/注册页、表单校验、加载与失败状态、未登录路由拦截。
- [x] 4.2 登录后进入个人 Agent 聊天页，右上角展示账号/角色/退出；首次管理员改密。
- [x] 4.3 实现 Hinter 用户目录、监管入口和用户角色管理，权限来源于服务端 capabilities。
- [x] 4.4 验证会话过期、撤权、刷新、多标签页与未发送草稿/已提交输入的恢复行为。

## 5. 旧数据迁移
- [x] 5.1 增加 SQLite/Markdown/文件引用盘点和 dry-run 命令，生成归属映射待办。
- [x] 5.2 实现可重跑导入与 legacy ID 映射，保留可见性、撤回/撤销和版本。
- [x] 5.3 实现授权管理员把未认领旧空间绑定到真实账户，拒绝按名称匿名认领。
- [x] 5.4 在切换窗口备份并停止旧写入，核对数量/引用/校验和；记录切换与回滚恢复点。

## 6. Compose、脚本与部署
- [x] 6.1 调整 `compose.yaml` / Dockerfile，加入 mysql、migrate、bootstrap-admin 和 agent-worker 服务及持久卷。
- [x] 6.2 调整 `compose.external.yaml`，外部模型路径保留数据库/schema/bootstrap 依赖。
- [x] 6.3 调整 docker-up/down/entrypoint 与本地启动脚本，增加配置验证、阶段失败退出、readiness 等待和凭据文件位置提示。
- [x] 6.4 更新 `.env.example`，生成本机 secrets 文件；避免输出明文密码、完整 DSN 和模型密钥。
- [x] 6.5 提供数据库/文件卷备份、恢复、显式 reset、升级和运维管理员恢复文档及命令。

## 7. 部署后验收
- [x] 7.1 全新卷部署：普通注册、手机密码/邮箱密码登录、Hinter Admin 首次登录改密。
- [x] 7.2 授予/撤销 Hinter 权限、最后管理员保护、跨用户与 URL 伪造访问拒绝、已有连接撤权。
- [x] 7.3 MySQL/应用/worker 重启后账号、角色、记录、记忆与 Run 状态保留。
- [x] 7.4 验证本地模型/外部模型两条启动路径、迁移失败、数据库断开、备份恢复与旧数据导入重跑。
- [x] 7.5 联合通用 Agent change 验收登录后聊天、Agent 信息与真实 DeerFlow 工具链路，记录实际结果。
