# 账户、权限与 MySQL 设计

## 1. 采用的产品约定

本文将用户口述的 Toker/Talker 统一记为 **Talker**，Hint/Hinter 管理侧统一记为 **Hinter**。

- 同一套注册账户可以同时拥有普通用户和管理员能力，无需为 Hinter 再维护一套手机号、邮箱或密码。
- 首期普通注册要求手机号、邮箱和密码，显示名可选且可编辑；未填写时使用不额外暴露完整联系方式的默认显示名。手机号或邮箱任一项配合密码登录；不接短信/邮件验证码。
- 手机号显式带国家/地区代码并标准化，邮箱采用明确、统一的大小写归一化规则；两种标识都唯一。未验证状态单独保存，不能宣称已验证或用于可信自助找回。
- 公开注册只授予 `talker`。想成为 Hinter 的人也先注册，由管理员在用户管理页授予权限。
- 一期两个角色：`talker` 和 `hinter_admin`，后者在界面称“Hinter 管理员”。不额外增加超级管理员层级。
- 默认管理员账户显示名 **Hinter Admin**，拥有上述两个角色。其管理员权限与其他被授予的管理员相同。
- 作用范围为单应用：Hinter 管理员可以管理注册用户并监管其 Agent 空间；普通用户仅访问自己的空间。后续按团队/人员限定的授权可扩展 `scope`，一期不虚构已实现的范围管理。

## 2. 登录态与账户安全

密码使用 Argon2id 等可升级的专用密码哈希，保存算法/参数信息；禁止明文或可逆加密保存用户密码。普通用户密码首期不少于 12 字符，接受长口令和密码管理器生成值，具体长度上限和错误码统一前后端实现。

首期使用服务端有状态会话：浏览器持有高熵随机 opaque cookie，MySQL 只保存会话令牌摘要、期限、撤销状态和认证版本。Cookie 为 HttpOnly、SameSite=Lax；HTTPS 部署启用 Secure，本地 HTTP 模式需显式配置。状态变更接口检查 CSRF token 与来源；密码、原始会话令牌不放 URL、浏览器持久存储、审计事件或普通日志。

认证成功返回用户资料、角色和服务端计算的 capabilities。权限检查读取当前账户状态与角色版本，不能把客户端的 `role`、旧 JWT 声明或隐藏按钮当授权依据。登录失败信息不区分账号不存在与密码错误，按账号标识及来源进行有界限流。

修改密码、禁用账户、退出或角色撤销会使相应会话/权限版本失效。新的 HTTP 请求必须重新校验；已有 WebSocket/SSE 连接在继续发送受限数据前重新检查并停止失权订阅。运行中的 Agent/工具任务同样检查授权变化，不能借旧 Run 继续访问刚撤销的管理员资源。

## 3. Hinter Admin 初始化

部署初始化在数据库迁移成功后执行一次：

1. 创建两个角色和基础权限，使用稳定 seed key 幂等写入。
2. 若 bootstrap 管理员尚不存在，创建显示名 `Hinter Admin` 的账户。部署者首次启动时自行输入唯一登录名和密码；可另外提供真实手机号/邮箱，不伪造联系方式。
3. 管理员初始密码由部署者自行设置，长度为 12–256 字符。普通注册用户也自行设置密码，不共享默认密码。
4. 密码只以哈希入库；部署输入保存在本机忽略提交且权限为 `0600` 的初始化配置文件，不在 Compose 常规日志中打印密码。
5. 首次登录进入改密流程，完成后才允许监管和角色管理。系统不提供读取旧密码接口。
6. 再次部署/重启只确认已初始化，不重置密码、不复活已撤销角色。管理员首次创建与强制改密状态在 MySQL 中持久化。

管理员可以授予/撤销已有用户的管理员权限，操作记录 actor、target、前后角色、时间和 request_id。事务内防止两个并发操作共同删除/禁用最后一个有效管理员。已安装系统若因人工误操作失去管理员，使用受控运维恢复命令并审计，不能靠普通重启重新提权。

## 4. RBAC 与所有权

| 权限 | Talker | Hinter 管理员 |
| --- | --- | --- |
| `chat.use` | 自己的空间 | 自己的空间 |
| `records.read` / `memory.manage` | 自己的空间 | 自己及被选中用户空间 |
| `workspace.supervise` | 无 | 所有注册用户空间 |
| `guidance.write` | 无 | 所选用户会话的 Hinter 协作输入 |
| `users.read` / `users.manage_status` / `roles.assign` / `legacy.bind` | 无 | 用户列表、状态、Talker/Hinter 授权与旧空间归属映射 |
| 读取密码/会话密钥 | 无 | 无 |

管理员选中用户 A 的空间时，`actor_user_id=管理员`，`owner_user_id=A`。Agent 上下文、子任务、数据库查询、记忆和文件目录使用同一映射；管理员自己的个人空间仍属于其本人。普通用户不能通过替换 thread_id、talker_id、音频路径、record_id、run_id 或下载 URL 访问 A。

`/auth/demo` 在目标部署中不存在或明确禁用；旧 URL 的 `role=hinter` 只可作为导航意图，不能登录或提权。API、WebSocket、音频和 artifact 下载均执行授权。旧公开音频链接不能继续绕过新的所有权检查。

## 5. MySQL 权威存储范围

目标使用 **MySQL 8.4 LTS** 独立容器，实施时固定已验证 patch/digest。所有业务文本与运行状态统一进入 MySQL，不保留 SQLite/Markdown 作为运行时双写权威源。

| 表/逻辑存储组 | 主要内容 |
| --- | --- |
| `users` | 联系标识、显示名、密码哈希、状态、认证版本、强制改密标记 |
| `roles` / `permissions` / `role_permissions` / `user_roles` | 角色、权限、授予人与关系版本 |
| `auth_sessions` / `security_audit_events` | 登录会话摘要、期限、撤销与授权审计 |
| `workspaces` / `threads` | 账户所有权、Agent 配置引用、旧 Talker 映射 |
| `conversation_records` / `record_revisions` | 用户/助手/控制记录、转写及修订 |
| `agent_runs` / `run_events` / `task_leases` | 通用任务、执行尝试、事件序列、领取租约 |
| `record_projections` | 首期问题形式及来源版本；后续摘要复用 |
| `memory_documents` / `memory_revisions` / `guidance_records` | Wiki 文本、长期记忆、私有备注及撤销版本 |
| `artifact_metadata` / `idempotency_keys` / `application_settings` | 文件引用、幂等响应与非秘密业务配置 |
| DeerFlow checkpoint / Store provider | 原生 thread/namespace/checkpoint、pending writes、channel/version 和持久 Store 数据 |
| `schema_migrations` / `legacy_import_map` | 数据库版本、旧主键和归属映射、导入校验结果 |

表名允许在 ORM 设计时调整，但覆盖范围、唯一约束和隔离契约不能省略。不得仅把完整会话 JSON 改存在 MySQL 一张表就宣称完成用户、权限与 Run 持久化。

字符集使用 utf8mb4；UTC 保存时间，展示遵循用户时区。所有资源具有可索引 owner/thread/run 键；手机号、归一化邮箱、角色关系、输入幂等键和事件序列有数据库唯一约束。并发更新采用事务与版本号，跨进程不依赖 Python 内存锁。

录音、上传附件、模型缓存、沙箱临时文件保留在持久卷或后续对象存储；MySQL 保存所有权、引用、校验与生命周期。Markdown 可作为导入/导出格式或临时工具文件，长期记忆和权限判断以 MySQL 内容为准。部署密钥使用受控 secret 文件/环境注入，数据库中只保存引用或适当保护后的材料，不将明文模型密钥散入业务日志。

## 6. DeerFlow/MySQL 适配

官方当前配置和数据库引擎主要支持 memory、SQLite、PostgreSQL；本项目采用 MySQL，因此必须实现受控 provider/集成补丁，不能只修改 URL，也不能额外启动 PostgreSQL 来绕过用户的存储要求。

- 产品 repository 采用 SQLAlchemy + MySQL driver，数据库 schema 用 Alembic 等版本化迁移管理。
- checkpoint provider 完整保留固定版本 LangGraph/DeerFlow 的 checkpoint、pending writes、namespace 与版本语义，支持实际使用的同步/异步入口；不能用一个最终答案快照冒充 checkpoint。
- DeerFlow Store、应用 thread/run/event 存储、默认文件型记忆与可变 Agent 元数据均有 MySQL 落点；配置模板仍可版本化在代码中。
- 统一数据库配置与 session factory，禁用默认 SQLite 或文件状态回退。数据库/provider 不可用时 readiness 失败，并清晰报错。
- 用真实运行时验证工具执行、子任务状态、记忆读写和 worker 重启恢复，记录与上游固定版本的兼容矩阵。

## 7. 迁移、归属与回滚

旧的命名 Talker 不是经过注册认证的账户，不能仅按名字/URL 自动授予所有权或管理员权限。

迁移分为：离线备份 SQLite/Wiki/文件 → 迁移 MySQL schema → 导入 legacy 标识与记录 → 显式账户归属映射 → 校验 → 切换运行配置。无法确认归属的空间标记 `unclaimed_legacy`，只在管理员迁移管理中可见，普通注册用户不能自行认领。不得给旧 Talker 批量生成同一个登录密码。

归属映射保留旧空间边界，不隐式合并不同 Talker 的 Wiki。已注册账户的默认空空间与导入空间通过显式默认空间指针选择，不因显示名相同覆盖已有记录。

导入保留稳定主键映射、时间顺序、旧作者、可见范围、撤回/撤销记录、Wiki 修订和文件引用。`legacy_import_map` 以来源、对象类型、旧 ID、版本唯一去重，支持 dry-run、重跑、断点与失败报告。校验至少包括行数、引用完整性、文本/文件校验和及管理员指定的归属映射。

切换窗口停止旧写入；成功切换后只写 MySQL，旧库与 Markdown 备份只读保留。回滚先备份新数据库并核对切换后的新增数据，不承诺把新账户/新 Run 无损写回旧 SQLite 模式。恢复方案应说明恢复点、文件卷配套备份与重新验证步骤。

## 8. Docker Compose 与脚本调整

目标服务与顺序：

```text
mysql healthy
  → migrate successfully
    → bootstrap-admin successfully
      → web + agent-worker ready
```

- `mysql`：独立镜像、应用数据库与非 root 应用账号、数据库健康检查、独立命名卷；默认不发布宿主机 3306。
- `migrate`：一次性 schema 迁移，使用数据库锁串行化，失败返回非零；不要让多个 web/worker 实例同时建表。
- `bootstrap-admin`：一次性幂等 seed，写受控凭据文件，依赖成功迁移。
- `web`：现有前端构建和接入 API，等待数据库/schema/bootstrap；对浏览器暴露现有 8818 端口。
- `agent-worker`：真实 DeerFlow 执行服务，与 web 使用相同数据库、身份映射和配置版本；队列与事件由 MySQL 共享。
- 持久卷区分 `mysql-data`、`agent-files` 与模型缓存。停止服务默认保留这些卷；数据销毁使用独立显式命令。
- Ollama/model-init 保持可选。本地模型路径等待模型准备，外部模型路径仅移除模型依赖，**仍保留 MySQL、迁移和 bootstrap 依赖**；修正当前 `compose.external.yaml` 的整体 `depends_on: !reset {}` 写法。

待调整脚本包括：`scripts/docker-up.sh`、`scripts/docker-down.sh`、`scripts/docker-entrypoint.sh`、本地 `start.sh`/`build.sh` 的配置检查，新增迁移/legacy 导入/数据库备份恢复入口；同步 `compose.yaml`、外部模型 override、Dockerfile 与 `.env.example`。

启动脚本先准备未存在的本地 secrets，再选择模型 profile、启动数据库并执行迁移/seed、启动应用、等待 readiness，最终输出访问地址和首次凭据文件位置。数据库密码含特殊字符时必须正确编码或使用独立连接参数。脚本不能打印解析后的完整环境或 DSN，不能在每次启动时安装可变版本的 DeerFlow。

部署验收需要覆盖首次安装与复用已有卷两条路径；`/healthz` 可代表进程存活，单独的 readiness 必须核对 schema、MySQL 和运行时 provider。MySQL 启动成功不等同注册登录已可用。

## 9. 页面流程

未登录访问聊天/管理 URL → 展示登录页并保留安全的站内返回路径 → 可切换注册 → 密码登录成功 → 普通用户直接进入个人 Agent 聊天页。

右上角展示显示名、Talker/Hinter 角色及退出动作；账户会话以 `/auth/me` 的服务端结果为准。管理员有“我的 Agent / Hinter 工作台 / 用户与权限”入口；普通用户不出现管理入口。会话过期显示登录页，避免丢失当前未发送的页面内草稿；已提交输入通过幂等键与 Run ID 恢复，不能自动重复提交。

用户管理页提供手机号/邮箱/显示名搜索、状态、角色、授予/撤销管理员操作和结果反馈。新注册用户首次登录即拥有自己的空间与 Agent 信息，不通过匿名创建一个同名 Talker 获得访问权。
