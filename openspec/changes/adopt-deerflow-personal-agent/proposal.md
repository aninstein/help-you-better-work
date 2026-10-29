# 全面接入 DeerFlow 的个人员工 Agent

## Why

产品从双通道问答 Demo 转向长期使用的个人员工 Agent，会议记录是首个默认工作场景。当前 `feature/deerflow-agent` 只加入了自研 LangGraph 节点，没有实际复用完整 DeerFlow 运行时，旧的逐题路由、审核和补答规则仍影响通用交流。

2026-10-25 用户明确选择全面采用 DeerFlow，淡化历史问答行为，并要求当前保留问题形式的展示、后续可以扩展为摘要形式。本 change 已按批准规格实施，详见实施记录。

## What Changes

- **BREAKING**：所有交互式 Agent Run 使用真实 DeerFlow 2.x lead agent、工具循环、技能、子 Agent 和记忆接口；退役自研问答决策图与独立答案推理链。
- 将核心身份改为“个人员工”，提供会议记录助手工作配置：理解连续发言、保留记录、答疑、整理决策与待办、按授权查询资料和执行工具。
- 原始输入、转写、控制消息、Agent 输出、工具事件统一记录；问题形式成为可替换的展示投影，不再决定 Agent 是否执行。
- 首期保留问题列表与来源关联；摘要视图作为后续扩展，不宣称本期已有自动摘要界面。
- 保留聊天、上传语音和实时通话输入适配，以及授权 Hinter 的私有协作通道；业务层不再另建问答 Agent。
- 持久化由配套登录/MySQL change 提供，包含 Run、checkpoint、记忆和记录，不使用默认本地文件状态作为权威存储。

## Capabilities

### New Capabilities

- `personal-employee-runtime`：真实 DeerFlow 执行核、个人员工与会议工作配置、授权上下文、工具与任务生命周期。
- `meeting-conversation-records`：通用记录、转写修订、流式输出和独立的问题展示投影。

### Modified Capabilities

无已归档 capability 的增量修改。现有 change 继续作为历史记录；冲突与替代关系见设计文档。

## Impact

- Backend：`app/agent/`、`application/service.py`、`calls.py`、`responses.py`、记录与流式事件 API。
- Frontend：登录后的聊天入口、Agent 信息、会话记录与问题列表，Hinter 切换入口受服务端权限控制。
- Runtime：固定版本的 DeerFlow 发行依赖与适配层、独立 `agent-worker` 服务、MySQL 持久化接口。
- Prompts：以通用员工/会议助手模板替代历史逐题分类、固定拒答和逐题人工审批模板。

## Dependencies and Scope

- 与 [`add-account-rbac-mysql`](../add-account-rbac-mysql/proposal.md) 共同交付；后者先提供账户、权限、数据库、迁移和 Compose 基础。
- 本期不建设逐题历史扫描与自动补答平台，不恢复历史逐题审核规则。
- 不默认打开任意 shell、外部消息发送或第三方账号权限；通用 Agent 的工具范围仍由配置和操作者授权决定。
- 已实现并切换新服务入口，运行证据见 implementation-status.md。
