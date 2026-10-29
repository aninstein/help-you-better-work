# 研究与技术决策

## 依据与约束

设计依据为飞书《双通道实时语音 Agent 技术选型与设计》及现有 UI 契约。固定不变量：未审核不得生成；Intake 不得回答；每个批准项仅一个 active generation；跨客户记忆不得进入上下文；Talker 永不收到 hint/reviewer/internal memory evidence；AI 文本完成后自动启动 TTS。

## R1：一期拓扑

**决策**：一个 React/TypeScript SPA + 一个 FastAPI Web 服务 + 一个服务内自研 Agent 模块。

**理由**：面试讨论可集中于控制面、安全和并发，而非服务治理；单体内仍以端口/adapter 分层，后续可拆分。FastAPI 对异步 WebSocket/SSE、OpenAPI、Python AI 生态友好。

**否决**：多微服务增加分布式事务与运维成本；DSH/LangGraph 会模糊“谁拥有状态转换”。

## R2：Agent 与工作流

**决策**：确定性状态机拥有业务状态；LangChain 只封装 ChatModel、structured output、PromptTemplate 与 Tool adapter。

**理由**：Review Gate、generation fence、重试与权限必须可推理、可测试。模型输出先做 schema 验证，失败不推进状态。

## R3：持久化与并发

**决策**：PostgreSQL 是事实源；Redis 仅用于连接 presence、短期缓存、事件 fan-out、generation lease。批准事务写入 ReviewDecision、GenerationCommand 与 outbox；数据库部分唯一索引保证同一 review scope 最多一个 active generation，Redis fence token 加速抢占但不是唯一正确性来源。

**备选**：仅 Redis 无法可靠审计；仅数据库可行但实时 fan-out 与短租约成本更高。

## R4：实时协议

**决策**：WebSocket 主通道，SSE 只读降级；游标恢复以数据库事件/outbox 为准。事件具备单会话递增 sequence，客户端按 event_id 幂等。

**理由**：WebSocket 支持双向低延迟；SSE 更易穿透代理。命令仍走 HTTP，避免在两种实时协议中重复写命令语义。

## R5：角色隔离

**决策**：认证令牌提供 tenant/user/role；仓储查询强制 tenant/customer scope；响应 DTO 使用 Talker/Hinter 两套显式 schema，事件在发布前投影，禁止“先返回完整对象再由前端隐藏”。

**验证**：字段级契约测试对所有 Talker HTTP/WS/SSE payload 递归扫描禁用字段与 canary 值。

## R6：记忆

**决策**：三层记忆均带 tenant_id/customer_id；当前轮次按 session 查询，短期按时间窗/未完成任务，长期按稳定画像。每层独立限额，按 P0→P1→P2 装配；低优先级不得覆盖高优先级事实，冲突在 generation context 中保留来源供 Hinter 审计。

## R7：语音

**决策**：STT adapter 接收音频对象并产出 interim/final；仅 final 创建可 Intake 的 SourceMessage 版本。原音频存对象存储，数据库保存受控引用。Answer 文本持久化后发出 TTS 命令；TTS adapter 返回音频资产。浏览器自动播放失败是 UI 状态而非 TTS 失败。

## R8：供应商与失败策略

- STT/TTS/LLM 均定义 protocol/adapter，供应商错误映射为统一错误码。
- 外部调用超时 + 有界指数退避；只对可重试错误重试。
- LLM structured output 解析失败可修复重试一次，仍失败进入 `failed_retryable`，不得猜测状态。
- TTS 失败保留文本答案并允许手工重试；STT 失败保留音频并允许重新转写。
- Redis 故障时暂停新 generation 或退化到 PostgreSQL advisory lock，不牺牲唯一生成不变量。

## R9：可观测性

OpenTelemetry trace 贯穿 request/session/source/question/review/generation/answer/audio；结构化日志禁止记录音频、hint 全文和完整记忆。核心指标：STT final 延迟、Intake 延迟、审核等待、首 token、文本完成、TTS ready、端到端延迟、重连恢复率、重复命令抑制数、投影拒绝数与跨 scope 查询拒绝数。
