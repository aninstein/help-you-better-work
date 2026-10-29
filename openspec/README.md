# Duet AI OpenSpec

最新实施（**已按批准规格部署，详见验收记录**）：[个人员工 Agent 与账户平台总览](personal-employee-plan.md)。2026-10-25 用户明确选择全面接入 DeerFlow，转向通用个人员工/会议记录助手，并引入真实账户、Hinter RBAC 和 MySQL Compose 部署：

- [全面 DeerFlow 个人员工 Agent](changes/adopt-deerflow-personal-agent/proposal.md)：[设计](changes/adopt-deerflow-personal-agent/design.md) · [规格](changes/adopt-deerflow-personal-agent/specs/) · [接口](changes/adopt-deerflow-personal-agent/contracts.md) · [实施任务](changes/adopt-deerflow-personal-agent/tasks.md)。问题形式保留为通用记录的展示投影，摘要形式后续扩展。
- [注册登录、Hinter RBAC 与 MySQL 部署](changes/add-account-rbac-mysql/proposal.md)：[设计](changes/add-account-rbac-mysql/design.md) · [规格](changes/add-account-rbac-mysql/specs/) · [接口](changes/add-account-rbac-mysql/contracts.md) · [实施任务](changes/add-account-rbac-mysql/tasks.md)。初始管理员凭据在部署时由操作者设置；也包含旧数据归属迁移、运行时存储适配和部署后验收。

以下增量保留为既有设计与实施记录；新规划中的替代关系在各自设计文档中说明，以各自实施记录为准。

最新增量：[上下文关联的 Hinter 补充任务](changes/contextualize-hinter-input/proposal.md)，将原始提问、所需人工帮助与 Hint 原文组合生成英文补答。[设计](changes/contextualize-hinter-input/design.md) · [实际验证](changes/contextualize-hinter-input/implementation-status.md)。

最新增量：[Talker 空间与统一 Hinter 工作台](changes/manage-talker-workspaces/proposal.md)，已实现首页创建/进入 Talker、Hinter 的 Talker → Session 目录与 Talker 所有的跨会话 Wiki。[设计](changes/manage-talker-workspaces/design.md) · [实施记录](changes/manage-talker-workspaces/implementation-status.md)。

最新实施（**核心功能已接入**）：[自然交流与异步 Hint 总览](response-flow-redesign.md)，拆成四个独立 change：

- [默认自动回答与少量例外](changes/default-auto-response/proposal.md)：文本、上传语音、电话默认自动，问题记录不再等同人工审核。
- [Hinter / Wiki 异步约束](changes/async-hinter-wiki/proposal.md)：独立 Hint 通道、跨模式持久限制、短句发布校验与撤回。
- [通话即时回应](changes/conversational-call-feedback/proposal.md)：先自然回应再处理慢任务，真实进度、持续倾听与原题补答。
- [文本与语音增量输出](changes/streaming-response-speech/proposal.md)：模型生成中即公开短句、合成和播放，全阶段可打断。

见[实施记录](response-flow-implementation.md)与[豆包/开源组件调研](response-flow-research.md)。新流程已替代默认逐题审核和整答串行总审；下方为此前增量的历史实施记录。

最新实施：[自然语音交互优化](changes/improve-natural-voice-interaction/proposal.md)。P0已接入本地音频判停、未完句等待、意图插话与候选降音、受控术语、按需直连校正、带序号的抗混叠音频采集。保留普通先答/人工补答、最新Wiki检查和撤回。协议3用于新客户端，协议2兼容固定三秒。[实施及实测边界](changes/improve-natural-voice-interaction/implementation-status.md) · [设计](changes/improve-natural-voice-interaction/design.md) · [任务](changes/improve-natural-voice-interaction/tasks.md)。条件性云语音P1尚未启用。

最新澄清与实施：[通话双状态、多问题先答与人工补答](changes/call-conversation-scheduling/proposal.md)。三秒为倾听到思考的转换，十秒为共享处理预算；普通问题先答，人工问题独立保留和后续补答。[技术设计](changes/call-conversation-scheduling/design.md) · [规格](changes/call-conversation-scheduling/specs/conversational-call-scheduling/spec.md)。

当前修复：[通话分轮、打断和英文校正](changes/fix-call-turn-taking/proposal.md)，**已按确认的规格实施**：默认约3秒有效静音聚合一个输入，独立处理插话；最终英文识别后走直连校正subagent，再交主Agent。[实施记录](changes/fix-call-turn-taking/implementation-status.md) · [技术设计](changes/fix-call-turn-taking/design.md) · [调研来源](changes/fix-call-turn-taking/research.md) · [功能任务](changes/fix-call-turn-taking/tasks.md)。

当前变更：[新增实时通话模式](changes/add-realtime-call-mode/proposal.md)。**第一版功能已实施**，持续 PCM 通话、自动短答、Hinter 提示和 Wiki 检查已跑通；范围与验证见[实施状态](changes/add-realtime-call-mode/implementation-status.md)。尚未开展原计划的全量性能/兼容性验收，不归档为全部规格完成。

- [提案与范围](changes/add-realtime-call-mode/proposal.md)
- [技术设计、时序与迁移](changes/add-realtime-call-mode/design.md)
- [开源组件与风控来源调研](changes/add-realtime-call-mode/research.md)
- [需求规格](changes/add-realtime-call-mode/specs/)
- [接口、事件与数据契约](changes/add-realtime-call-mode/contracts.md)
- [拟定策略配置](changes/add-realtime-call-mode/policy-seed.yaml)
- [实施任务与验收清单](changes/add-realtime-call-mode/tasks.md)

历史 `specs/001-dual-channel-voice-agent` 保留为原始设计；当前实现以代码和其 implementation-status 为准。本 change 用六个新增 OpenSpec capability 定义增量行为，因为仓库此前没有 `openspec/specs` 基线。归档实施完成的 change 时才提升为基线；本次不归档、不将提案当作已上线行为。

验证：`openspec validate add-realtime-call-mode --strict --no-interactive`、`openspec validate fix-call-turn-taking --strict --no-interactive`。
