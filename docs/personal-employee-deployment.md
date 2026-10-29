# 个人员工部署与运维

本版本默认入口为注册/密码登录后的个人员工聊天，运行核为官方 DeerFlow 2.1.0，commit `e9074b7126833b5ca4739bcedcbbed01b211246c`。`vendor/deerflow/provenance.json` 记录原始源码快照的 SHA256，`runtime/uv.lock` 固定依赖。升级时必须重新核对 embedded client、checkpoint、MemoryManager 和授权接口，禁止只改版本字符串。

## 首次启动

1. 复制 `.env.example` 中的新部署变量到本机 `.env`，保留原有模型配置。外部模型可继续用 `DEEPSEEK_OFFICIAL_API_KEY` 或 `MODEL_CONFIG_JSON`；模型密钥不会写入版本库。
2. 在交互式终端执行 `./scripts/docker-up.sh`，按提示自行设置初始管理员登录名与 12–256 字符密码。脚本将这两个值只写入被 Git 忽略的 `.runtime/bootstrap/bootstrap-admin.json`（0600），同时生成 `.runtime/secrets`，然后启动独立 MySQL 8.4.7、迁移、初始化管理员、web、agent-worker 和 projection-worker。非交互式首次部署须先在该路径安全地提供同结构、权限为 0600 的 JSON 文件。配置外部模型时不启动 Ollama，但仍保留数据库和初始化依赖。
3. 打开 `http://127.0.0.1:8818`。普通用户以手机号、邮箱、至少 12 字符密码注册，再以手机或邮箱登录。
4. 使用刚才设置的登录名和密码登录初始管理员；显示名为 `Hinter Admin`。首次登录必须修改密码。重复部署不重置密码、不恢复被撤销的角色。旧部署已存在的管理员与本机旧凭据文件仍可保留，无需重新设置。
5. 管理员在“用户权限”授予注册用户 Hinter 权限，在 Hinter 目录监管空间。角色或账号状态变更使该用户旧会话失效，正在执行的工具也会重新检查授权。

默认 MySQL 不映射宿主端口。调试宿主运行时可添加 `-f compose.local.yaml`，仅绑定 `127.0.0.1:13316`。镜像下载受限时可设置 `MYSQL_IMAGE=docker.m.daocloud.io/library/mysql:8.4.7@sha256:0426ec38c7a10aa45ba383887df7878f74ee70e2fd589c7b69207f3577901903`，保留版本与摘要。Compose 构建通过 DUET_UID 对齐本机私密凭据文件的用户权限。

HTTPS 部署设置 `SESSION_COOKIE_SECURE=true` 与准确的 `APP_ORIGIN=https://your-host`。本机 HTTP 使用 `false`。Cookie 为 HttpOnly、SameSite Lax；所有登录态变更使用 CSRF token，并检查 Origin。没有公开 Demo 身份签发入口。

## 本机开发

```sh
python3 scripts/setup-employee.py
docker compose -p duet-employee -f compose.yaml -f compose.local.yaml up -d --wait mysql
./scripts/build.sh
./scripts/start.sh
```

`start.sh` 同时启动 web、交互 worker 与问题投影 worker，任一进程退出会停止其余进程。数据库不可用或凭据缺失会启动失败，不会降级写 SQLite。

`/healthz` 仅表示 web 进程存活；`/readyz` 检查 schema 与 worker 心跳。真实模型调用仍须通过会话验收，健康状态不能证明外部模型或检索服务可用。

## 旧数据切换

先停止旧写入，再使用 SQLite backup API 制作一致性副本，复制 Wiki/音频并保留原目录为只读回滚来源。容器旧卷和本机 `backend/data` 是两个来源，不能混用。

```sh
PYTHONPATH=backend runtime/.venv/bin/python -m app.employee.cli legacy-inventory --source IMMUTABLE_BACKUP --output .runtime/legacy-inventory.json
PYTHONPATH=backend runtime/.venv/bin/python -m app.employee.cli legacy-import --source IMMUTABLE_BACKUP --output .runtime/legacy-import.json
```

导入保留源 ID、完整 legacy JSON、可见性、撤回/撤销状态与 Wiki 路径边界。每个来源有独立映射和校验和；重跑不重复导入，源内容变化会失败。未映射空间为 `unclaimed_legacy`，只允许管理员读取。管理员通过 `POST /api/v1/admin/legacy-workspace-bindings` 显式指定 workspace_id 与已注册 user_id。禁止按名称自动认领，也不会覆盖用户新建默认空间。导入会按已验证引用复制音频到文件卷并创建 artifact metadata；未引用文件只保留在只读备份。恢复历史时间、只读问题投影，并封存原始控制/审计数据。

## 备份、恢复与停止

```sh
./scripts/backup-employee.sh .runtime/backups/release-1
./scripts/restore-employee.sh .runtime/backups/release-1 --replace-current-data
./scripts/docker-down.sh
```

备份暂停 web 和两个 worker 写入，保存 MySQL dump、文件卷与校验值；恢复显式要求覆盖参数并校验文件。普通停止保留全部卷。恢复旧版本前必须另外保存切换后的新增数据；直接启动旧 SQLite 应用不会自动合并 MySQL 新写入。

完全重置会永久删除账号、会话和运行状态，必须由操作者明确执行 `docker compose -p duet-employee -f compose.yaml down --volumes`，同时归档旧管理员凭据文件后重新 bootstrap。不要把此命令加入日常停止脚本。

管理员遗失密码时，在受控宿主执行：

```sh
PYTHONPATH=backend runtime/.venv/bin/python -m app.employee.cli recover-admin --user-id USER_ID --output .runtime/secrets/recovered-admin.txt
```

该命令显式恢复指定账号权限，撤销全部旧会话并要求首次改密，写授权审计；新文件必须不存在，避免覆盖其他凭据。不提供匿名找回密码接口。

## 持久化与退役边界

- MySQL 是账户、角色、Cookie 会话摘要、记录/修订、Run/events、任务租约、问题投影、个人记忆和迁移映射的权威来源。
- `employee/checkpoint.py` 实现原生 checkpoint typed serialization、pending writes、namespace、同步/异步 Saver/Store。写入带原任务的租约和授权检查；失败不回落内存。
- `employee/agent_memory.py` 实现官方 MemoryManager 插槽，读取明确 owner 的公开 MySQL 记忆；语义事实由真实 agent 调用 remember 保存。私有管理员备注不进入模型。
- embedded client 显式注入 Saver，并通过固定版本 provider shim 统一 native helper 的 Store。Gateway 原生 ORM 路由没有启动；应用状态由本项目 MySQL BFF 提供。不要误把 upstream 默认 database 配置理解为已支持 MySQL。
- 旧 `agent/decision_graph.py`、CallReasoner、ResponseService、Demo token、逐题审核和 backlog 调度保留作迁移参考，不再被 `app.main` 或新 API 引用。
- STT/TTS 仅作 IO；确认转写通过统一 inputs 创建 DeerFlow Run。音频播放中断不会取消后台任务，停止任务必须使用 Run cancel。
- 只开放代码中的服务端工具允许集。采用通用 Agent 不开放 shell、管理员接口、外发消息或任意文件写入。

## 语音与设备

STT 使用多语种 Whisper tiny，缓存位于 `/app/data/models/whisper`。可将已有模型的 `config.json`、`model.bin`、`tokenizer.json`、`vocabulary.txt` 放入其中的 `faster-whisper-tiny/`，避免运行时联网下载；没有预置时首次转写会下载官方模型。`WHISPER_MODEL` 和 `WHISPER_MODEL_CACHE` 可显式配置。准备模型也可通过容器中的 `app.employee.voice.recognizer()` 预热。单次上传转写有 60 秒等待预算，模型缺失/不可达会明确返回不可用；文字聊天继续工作。

朗读优先使用 Edge TTS；12 秒内不可用则使用镜像内置的 eSpeak 离线朗读。离线语音较机械。语音文件仍有记录关联、所有权检查与撤回控制。通话中新的发言会停止播放，挂断不取消后台 Run；强制停止任务应点击“停止任务”。麦克风仅通过浏览器权限启用。

本地 Ollama 在无 GPU 的 Docker 环境推理较慢，默认 CPU 上限为 4，可配置 `OLLAMA_CPUS` 和 `OLLAMA_NUM_THREAD`。当前外部模型部署不需要保持 Ollama 进程运行，模型卷保留供切换使用。

## 本次部署证据

见 [实施与验收记录](personal-employee-acceptance-20261025.md)。初始管理员密码与模型密钥均不写入文档或版本库。备份不包含宿主 `.env` 与秘密文件；异机恢复时应由操作者另外安全保管并提供这些部署配置。
