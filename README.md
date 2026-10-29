# Duet · 个人员工

基于官方 **DeerFlow 2.1** 的个人员工，当前工作配置为会议助手。保留会议记录、问题视图和个人记忆，后续可扩展摘要、行动项等展示形式。

## 使用许可

本仓库原创部分按 [Personal Study License 1.0](LICENSE) 仅供个人学习和私下实验；禁止商用、盈利性运营以及对外提供服务。这是“源码可见”的授权，不是 OSI 定义的开源许可证。DeerFlow 等第三方内容保留各自的许可证，详见 [第三方说明](THIRD_PARTY_NOTICES.md)。发布前仍需由相关权利人确认授权。

## 一键部署

```sh
# 可先将 .env.example 复制为 .env 并填写自己的外部模型 API，或使用默认 Ollama。
# 首次部署会提示输入管理员登录名和密码；终端输入不会写入 Git。
./scripts/docker-up.sh
```

打开 **http://127.0.0.1:8818**，以手机、邮箱和至少 12 字符密码注册，再通过手机或邮箱密码登录。右上角显示真实账号和角色。

初始管理员显示名 **Hinter Admin**；登录名和密码必须由部署者首次启动时自行输入，保存在被 Git 忽略的本机 `.runtime/bootstrap/bootstrap-admin.json`（0600）。首次登录必须改密。重复部署不修改已有密码或恢复已撤销角色。管理员可在“用户权限”授予注册用户 Hinter 权限，并通过 Hinter 目录监管空间、提供公开提示或私有备注。

Compose 管理独立 MySQL 8.4.7、schema 迁移、管理员初始化、web 与独立 agent-worker。外部模型启动路径保留数据库和初始化依赖。MySQL 默认不暴露宿主端口，业务文本、账号、角色、Run、checkpoint 与 Store 以 MySQL 为权威来源。

停止服务并保留数据：

```sh
./scripts/docker-down.sh
```

完整配置、开发模式、备份恢复、管理员恢复和旧数据迁移见 [个人员工部署文档](docs/personal-employee-deployment.md)。建议为首次构建预留至少 8 GB 可用磁盘空间；构建检查会验证原生运行时可以导入。

## 使用

- 登录后进入自己的聊天工作空间，可输入陈述、问题或任务；不需要先整理成问句。
- 每个输入先保存，再创建独立 Run。同一会话按顺序处理，后来的输入不会删除先前的任务。
- 问题视图是记录的展示投影，提取失败不阻塞主任务。摘要模式尚未开放，请求会明确返回 422。
- 个人记忆跨会话使用；Agent 只有在真实工具保存成功后才能宣称已记住。
- 语音消息先转写再由用户确认发送；连续通话把确认转写交给同一 Run 入口。停止播放/挂断与显式停止任务分离。
- Hinter 私有备注不进入用户或 Agent 上下文。作为公开事实的提示可以供后续任务使用。
- 不提供前端选角色提权或公开 Demo token。所有 REST、事件、音频和文件访问都检查服务器会话与归属。

## 火山实时语音

在根目录 `.env` 填写 `VOLCENGINE_API_KEY`；已有服务运行时执行 `./scripts/reload-voice.sh` 重载配置，首次部署使用 `./scripts/docker-up.sh`。默认使用豆包流式识别 2.0 和语音合成 2.0，资源与音色可配置；Key 需具备对应产品权限。实时语音输入边说边显示草稿，结束后二遍识别校正，确认发送才创建 Agent 任务。通话默认显示紧凑声波条，并隐藏文本输入区。

配置选项、协议与排队处理说明见 [火山语音接入](docs/volcengine-voice.md)。没有真实 Key 时不会声称已连接云端。

语音有效性与回答语言统一经过独立的 [输入预处理层](docs/input-preprocessing.md)：无效短片段在创建气泡前拦截，中英混合输入按当前请求的主要语言回答；快速分类模型可单独配置。

## 开发

Python 3.12、Node 22、pnpm 10、uv 和 Docker Compose。

```sh
python3 scripts/setup-employee.py
docker compose -p duet-employee -f compose.yaml -f compose.local.yaml up -d --wait mysql
./scripts/build.sh
./scripts/start.sh
```

- `backend/app/employee/`：账户、权限、MySQL repository、原生 DeerFlow 适配和 worker。
- `frontend/src/employee/`：登录、用户权限、Hinter、通用聊天与语音 IO。
- `runtime/`：固定依赖锁和版本化 Skills；`vendor/deerflow/`：官方源码快照与校验值。
- `openspec/changes/adopt-deerflow-personal-agent/`：通用 Agent Spec 与实施状态。
- `openspec/changes/add-account-rbac-mysql/`：认证、权限、存储与部署 Spec。
- `/docs`：当前 API；`/healthz`：进程存活；`/readyz`：数据库/schema/worker 就绪。

## 历史实现

旧问答决策图、逐题审核、SQLite/Markdown Wiki 和 Demo 角色入口已从新流量入口退役，源码保留作迁移参考。旧文档中的固定英文、匿名 Talker 或逐题补答行为不再是当前产品契约。
