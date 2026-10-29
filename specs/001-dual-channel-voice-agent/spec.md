# 功能规格：双通道实时语音 Agent（一期）

**Feature**：001-dual-channel-voice-agent  
**状态**：Ready for planning  
**日期**：2026-10-22  
**依据**：飞书《双通道实时语音 Agent 技术选型与设计》、`specs/README.md`

## 1. 目标与范围

为 Talker 提供连续语音/文字对话，为 Hinter 提供不可见于 Talker 的问题审核与 hint 通道。系统将输入确定性地转换为可审核问题，只有批准后才生成文本答案，并自动合成语音。

### 一期包含

一个 React/TypeScript 页面、一个 FastAPI Web 服务、一个服务内全自研 LangChain Agent；PostgreSQL 事实源、Redis 实时/租约；STT/TTS/LLM adapter；HTTP + WebSocket，SSE 降级；三层记忆与角色投影。

### 一期不包含

DSH、LangGraph、多微服务、自动绕过人工审核、Agent 自主决定业务状态、后端业务代码的本次实现、生产级供应商选定。

## 2. 角色

- **Talker**：提交语音/文字并接收公开问题状态、文本/语音答案。
- **Hinter**：查看问题队列、审核问题、注入私有 hint、查看内部 memory evidence。
- **系统/Agent**：Intake 和受控生成；不能自行批准。
- **运维/审计者**：通过受控日志、指标和审计事件定位失败，不默认访问内容明文。

## 3. 核心不变量

1. 未审核不得生成，任何 Answer 必须追溯到批准决定。
2. AI Intake 只筛选、拆分、归一化、去重，不回答。
3. 每个批准项同一时刻仅一个 active generation。
4. 跨 `tenant_id/customer_id` 记忆永不进入检索结果或生成上下文。
5. Talker 永不收到 hint、reviewer、审核备注、内部 memory evidence/ID、Prompt、Tool trace。
6. AI 文本完成后系统自动启动 TTS。

## 4. 用户故事与验收场景

### US1 Talker 提交语音（P1）

**场景：final 后进入 Intake**
- Given Talker 已进入活动会话并授权麦克风；When 上传音频且 STT 先发送 interim、后发送 final；Then UI 可显示 interim，但仅 final 创建可处理 SourceMessage 版本并触发一次 Intake，原音频可重播。

**场景：STT 失败**
- Given 原音频已持久化；When STT 超时；Then 消息标记 `transcription_failed`，保留音频并允许重试，不产生问题。

### US2 Intake 形成问题队列（P1）

**场景：拆分与去重**
- Given 一条消息包含三个表达，其中两个语义重复；When Intake 完成；Then产生两个 CanonicalQuestion，保留来源映射、归一文本、重复计数/置信度，状态为 `pending_review`，且无 Answer/TTS。

**场景：无问题输入**
- Given final transcript 只有陈述；When Intake 完成；Then记录 `no_question`，不创建问题，不生成回答。

### US3 Hinter 审核（P1）

**场景：批量批准并携带 hint**
- Given Hinter 选择多个 `pending_review` 问题；When 输入 hint 并批准；Then原子创建 ReviewDecision 与 GenerationCommand，hint 仅进入内部 generation context，选中项进入 `approved/generating`。

**场景：拒绝或过期版本**
- Given问题已更新或已审核；When用旧 version 再审核；Then返回 `409 VERSION_CONFLICT`，不创建 generation。

### US4 受控生成与语音（P1）

**场景：生成、TTS 与播放**
- Given有效 GenerationCommand；When LLM 完成文本；Then先持久化 Answer，再自动创建 TTS 任务；When音频 ready；Then前端尝试自动播放并支持暂停/继续/重播。

**场景：自动播放被阻止**
- Given浏览器拒绝 autoplay；Then UI 显示显式播放动作，文本答案保持 completed，不将其视为生成失败。

### US5 角色隔离（P1）

**场景：Talker 投影**
- Given会话包含 hint、reviewer 与 memory evidence canary；When Talker 读取任何 HTTP snapshot 或实时事件；Then上述字段和 canary 均不存在，标签最多显示 `Human reviewed`。

### US6 记忆装配（P1）

**场景：隔离与优先级**
- Given同名客户跨两个 tenant，且同 tenant 两个 customer 均有记忆；When为目标 scope 生成；Then只召回完全匹配 `tenant_id + customer_id` 的内容，并按当前轮次、短期、长期顺序装配；冲突时高优先级生效。

### US7 实时恢复（P2）

**场景：断线补发**
- Given客户端处理到 sequence 42 后断线；When携带游标重连；Then仅投影并补发 43+；重复 event_id 不触发副作用，缺口导致 snapshot resync。

## 5. 功能需求

- **FR-001** 系统必须从认证上下文确定 `tenant_id/user_id/role`，不得信任请求体中的角色或租户。
- **FR-002** 会话和所有领域实体必须绑定 tenant；客户数据实体必须同时绑定 customer。
- **FR-003** 系统必须支持 Talker 文字输入、语音上传和 STT interim/final；只允许 final 进入 Intake。
- **FR-004** 系统必须保留原音频受控引用、final transcript、语言、时间戳和来源关联。
- **FR-005** Intake 必须输出 schema 化结果：拆分项、normalized text、source spans、dedupe group/confidence；输出 schema 不得包含 answer。
- **FR-006** CanonicalQuestion 必须有稳定 ID、版本和 `pending_review/approved/rejected/generating/answered/failed` 状态。
- **FR-007** 问题队列必须支持分页、状态筛选、稳定顺序及 Hinter 单选/多选；已完成项不可再批准。
- **FR-008** Review Gate 必须校验角色、问题版本、同一 session/customer/tenant、可审核状态后再原子写决定。
- **FR-009** 未存在批准决定时，任何路径均不得创建 GenerationCommand 或调用 LLM。
- **FR-010** hint 必须关联 ReviewDecision/GenerationCommand，仅供 Hinter 和内部生成使用。
- **FR-011** 生成上下文按“批准问题 → 当前轮次 → 短期 → 长期 → hint 指令层”确定性装配；hint 不得伪造更高权限系统指令。
- **FR-012** 每项记忆查询必须包含 tenant/customer 条件；当前轮次还必须包含 session 条件。
- **FR-013** 每个批准 scope 同时最多一个 active GenerationCommand；采用数据库唯一约束、租约和递增 fence token。
- **FR-014** 所有写 API、外部回调和事件消费者必须支持幂等；同 key/同 payload 返回同结果，不同 payload 返回冲突。
- **FR-015** 过期 fence token 的 worker 不得提交 Answer、问题状态或 TTS 命令。
- **FR-016** Answer 文本持久化成功后必须由 outbox 自动触发 TTS，不依赖前端额外命令。
- **FR-017** UI 必须支持 AI 音频自动播放、暂停、继续、重播，并在 autoplay 被拒绝时降级为显式播放。
- **FR-018** 系统必须为原音频、AI 音频提供短期签名读取 URL；授权必须复核 tenant/customer/session/role。
- **FR-019** WebSocket 事件必须包含 event_id、session_id、sequence、type、occurred_at；SSE 提供同语义只读降级。
- **FR-020** 系统必须支持按 sequence 游标补发；游标过旧或缺口时返回 resync_required 并提供 snapshot URL。
- **FR-021** 后端必须按 Talker/Hinter 使用字段白名单 DTO 投影 HTTP、WS、SSE 与错误细节。
- **FR-022** Talker 投影必须删除 hint、reviewer、review_notes、memory_evidence/internal memory IDs、prompt、tool trace 和内部错误。
- **FR-023** 供应商 STT/TTS/LLM 必须经 adapter，统一超时、可重试分类、熔断和错误映射。
- **FR-024** STT 失败可从原音频重试；LLM 失败可安全重试同 GenerationCommand；TTS 失败不得删除文本答案且可独立重试。
- **FR-025** 状态转换与关键读取必须记录不可变审计事件，但日志不得包含敏感全文。
- **FR-026** UI 必须将当前 Mock 状态映射到契约状态，不得以客户端状态代替服务端门禁。

## 6. 非功能需求

- **NFR-001 性能**：在供应商正常的 P95 条件下，final transcript 后 2 秒内出现问题队列更新；审核后首个生成状态事件 <500ms；事件发布到在线客户端 P95 <300ms。
- **NFR-002 可用性**：Web 服务月可用性目标 99.9%；单供应商暂时故障不得破坏已持久化文本/音频引用。
- **NFR-003 一致性**：ReviewDecision、GenerationCommand、outbox 在同一数据库事务；最终答案提交采用 fence 条件更新。
- **NFR-004 安全**：越权读取/写入返回 404/403 且不泄露实体存在性；对象 URL 最长 5 分钟；传输与静态存储加密。
- **NFR-005 隐私**：音频、transcript、hint 与记忆设置可配置保留期和删除流程；默认结构化日志不记录正文。
- **NFR-006 可观测性**：所有链路包含 correlation_id、tenant/session 与脱敏实体 ID；提供延迟、失败、重试、幂等命中、fence 拒绝、重连指标。
- **NFR-007 恢复**：网络恢复后 5 秒内开始游标补发；重复/乱序事件不得导致重复生成或重复播放。
- **NFR-008 可测试性**：adapter 可替换为 deterministic fake；核心状态机、投影和隔离逻辑不依赖真实供应商。
- **NFR-009 无障碍**：关键操作支持键盘与可见焦点；音频始终有文字替代；状态不只依赖颜色。
- **NFR-010 容量基线**：单实例基线支持 500 活动 WS、每会话 5 msg/s 事件突发；压测后再确定扩容阈值。

## 7. 边界条件

- final 重复回调：按 provider event/idempotency key 只处理一次。
- 一个 SourceMessage 拆出零个或多个问题；source span 可多对多。
- 语义疑似重复但低置信度：保留独立问题并标记，不静默删除。
- 批准批次中任一问题版本冲突：整批失败，不部分生成。
- 生成期间问题被撤销：撤销命令使 lease 失效；旧 worker 回写被 fence 拒绝。
- hint 含提示注入：作为不可信用户内容封装，不得修改系统策略、角色投影或 tool allowlist。
- Redis 不可用：以 PostgreSQL 锁/唯一约束保证正确性，允许降低吞吐。
- TTS 慢于客户端重连：`audio.ready` 可经补发或 snapshot 恢复。
- URL 过期：重新授权签发，不暴露底层对象 key。

## 8. 成功标准

- **SC-001** 自动化测试证明 100% 未批准生成请求未调用 LLM。
- **SC-002** 20 并发批准/生成竞争仅产生 1 个 active generation。
- **SC-003** canary 隔离测试中 Talker payload 与错误/实时事件泄露数为 0。
- **SC-004** 跨 tenant/customer 记忆污染测试泄露数为 0，且三层顺序正确。
- **SC-005** Answer completed 后 100% 产生且仅产生一个逻辑 TTS command（允许同命令重试）。
- **SC-006** WS 断线恢复测试无丢失、无重复副作用；过旧游标可完成 snapshot resync。
- **SC-007** 前端 frozen-lockfile 安装与生产构建通过，Talker/Hinter/Memory 三个入口可操作。
