# 执行任务：双通道实时语音 Agent（一期）

> 此文件保留原始生产规格的任务规划。后续用户将本次范围调整为完整可用 Demo，并明确简化生产门禁、采用 Prompt 驱动的用户 Markdown Wiki；当前实现、验证和完成状态见 [实现状态](implementation-status.md) 与 [技术实现文档](../../docs/technical-implementation.md)。下面尚未勾选的生产能力不等于本次 Demo 的遗漏项。`[P]` 表示原规划中的可并行项。

## 当前 Demo 完成摘要（2026-10-22）

- [x] React/FastAPI 单服务网页、Talker/Hinter 固定身份、共享 Session 与 WS/SSE 快照更新。
- [x] 输入/语音转录、Intake 拆题、人工草稿审核、确认发布、取消、单项/批量拒绝、英文回答和英文 TTS。
- [x] Hinter AI 模式、草稿编辑、Hint 润色、版本冲突防护、问题等待计时和 3/6 分钟红橙提醒。
- [x] 两个用户 ID 隔离的 Markdown Wiki、Prompt 驱动整理、二次确认的全量 Wiki 删除。
- [x] `.env` 模型主备回退、Docker Compose、基础接口/浏览器/音频验收。
- [ ] PostgreSQL/Redis/outbox、OIDC、真实多租户、生产容量及原计划完整 NFR 门禁（后续生产化项）。

## Phase 0：仓库与规格基线

- [x] **T001** 迁移 React/TypeScript/Tailwind UI、锁文件与配置，排除 dist、tsbuildinfo、site.meta。
- [x] **T002** 将 UI 说明改写为“一期 UI 原型与前端交互契约”。
- [x] **T003 [P]** 建立 constitution、spec、plan、research、data-model、quickstart。
- [x] **T004 [P]** 建立覆盖关键资源与角色视图的 OpenAPI 3.1 契约。
- [x] **T005** frozen-lockfile 安装并执行前端生产构建，记录结果。

## Phase 1：后端骨架与领域内核

- [x] **T006** 初始化 `backend/` Python 3.12、FastAPI、Pydantic、pytest、lint/typecheck，提供单服务 API entrypoint 与健康检查。
- [ ] **T007 [P]** 实现配置、OIDC/JWT 认证映射与 `TenantCustomerScope`，禁止客户端指定 role/tenant。（FR-001/002）
- [ ] **T008 [P]** 建立领域实体、错误码和显式状态转换表。（FR-006/025）
- [ ] **T009** 为状态机编写属性测试：无批准无 generation、非法转换全拒绝。（FR-009，SC-001）
- [ ] **T010 [P]** 定义 STT/TTS/LLM/ObjectStore/Clock/EventBus protocols 与 deterministic fake adapters。（FR-023，NFR-008）

## Phase 2：数据库、事件与幂等

- [ ] **T011** 编写 PostgreSQL migration：所有实体、组合 scope 外键、版本列、保留期字段。（FR-002）
- [ ] **T012** 添加 active generation 部分唯一索引、fence token 与 lease 字段。（FR-013/015）
- [ ] **T013 [P]** 实现 IdempotencyRecord middleware/repository，校验 request hash。（FR-014）
- [ ] **T014 [P]** 实现 DomainEvent/outbox、单 session sequence 与幂等 consumer。（FR-019/020）
- [ ] **T015** 集成测试事务原子性、重复投递、同 key 异 payload 冲突。（NFR-003）
- [ ] **T016** 并发测试 20 个批准/生成请求和 lease 接管，证明仅一 active 且旧 fence 失败。（SC-002）

## Phase 3：消息、STT 与 Intake

- [ ] **T017 [P]** 实现会话创建/读取与 Talker 文字 SourceMessage API。（FR-003）
- [ ] **T018 [P]** 实现音频上传、对象校验、短签读取授权与原音频保留。（FR-004/018）
- [ ] **T019** 实现 STT adapter 编排、interim 事件、final 幂等提交和失败重试。（FR-003/024）
- [ ] **T020** 定义 Intake structured output schema（无 answer 字段），实现 split/normalize/dedupe 及 schema 校验。（FR-005）
- [ ] **T021** 持久化 QuestionSourceLink、稳定 display_no、重复置信度与 `pending_review` 队列。（FR-006/007）
- [ ] **T022 [P]** 测试 0/1/N 问题、复合问题、低/高置信重复、重复 final callback。（FR-005）
- [ ] **T023** 安全测试恶意 Intake 输出不能生成 Answer 或推进 approved。（SC-001）

## Phase 4：Review Gate 与角色投影

- [ ] **T024** 实现问题列表分页/筛选/版本以及 Hinter 批量选择所需 API。（FR-007）
- [ ] **T025** 实现批量审核事务：角色、scope、版本、状态全量校验；任一冲突整批回滚。（FR-008）
- [ ] **T026** 原子创建 ReviewDecision、GenerationCommand 与 outbox；hint 加密保存。（FR-009/010）
- [ ] **T027 [P]** 实现 Talker/Hinter 两套 session/message/question/answer DTO 白名单 projector。（FR-021/022）
- [ ] **T028 [P]** 实现相同 projector 的 WS/SSE event 映射与安全错误映射。（FR-021）
- [ ] **T029** 用 hint/reviewer/memory canary 做 HTTP、WS、SSE 递归泄露测试。（SC-003）

## Phase 5：三层记忆与受控生成

- [ ] **T030 [P]** 实现 turn/short/long MemoryEntry 写入、查询与保留策略。（FR-012）
- [ ] **T031** 实现强制 tenant/customer scope 的 Memory repository；turn 强制 session。（FR-012）
- [ ] **T032** 实现 P0→P1→P2 分层预算、冲突规则及 Hinter-only evidence。（FR-011/012）
- [ ] **T033** 实现 Generation worker：租约、fence、批准问题装配、LangChain 模型调用、条件提交。（FR-011/013/015）
- [ ] **T034 [P]** 将 hint 作为不可信私有上下文，锁定 system policy/tool allowlist。（FR-010）
- [ ] **T035** 跨 tenant、同 tenant 跨 customer、同 customer 三层优先级 canary 测试。（SC-004）
- [ ] **T036** LLM timeout/structured-output 错误/worker crash 的有界重试与恢复测试。（FR-024）

## Phase 6：Answer、TTS 与实时恢复

- [ ] **T037** 实现 Answer 文本提交事务并自动写唯一逻辑 `tts.requested` outbox。（FR-016）
- [ ] **T038** 实现 TTS adapter、AudioAsset、独立重试；失败保留文本。（FR-016/024）
- [ ] **T039 [P]** 实现 WS 鉴权订阅、心跳、sequence 补发、gap/过旧游标 resync。（FR-019/020）
- [ ] **T040 [P]** 实现 SSE 同语义只读降级，所有事件发布前 role projection。（FR-019/021）
- [ ] **T041** 测试 Answer 完成后自动且逻辑唯一 TTS、供应商重复 callback。（SC-005）
- [ ] **T042** 测试断线、乱序、重复、游标过旧、snapshot 恢复无重复副作用。（SC-006）

## Phase 7：前端契约落地

- [ ] **T043** 将本地 Mock 替换为 typed API client 与 session snapshot reducer。（FR-026）
- [ ] **T044 [P]** 接入录音上传、STT interim/final 与原音频播放。
- [ ] **T045 [P]** 接入 Hinter 队列、批量审核、version conflict 与 idempotency key。
- [ ] **T046** 接入 WS/SSE、event_id 去重、sequence gap snapshot resync 与连接状态。
- [ ] **T047** 实现 TTS autoplay、被拒绝降级、暂停/继续/重播与可访问标签。（FR-017，NFR-009）
- [ ] **T048** 前端测试确保 Talker 组件不接收私有 schema，完成三入口 E2E。（SC-007）

## Phase 8：可观测性、隐私与发布门禁

- [ ] **T049 [P]** 接入 OpenTelemetry trace、结构化脱敏日志与 correlation ID。（NFR-006）
- [ ] **T050 [P]** 建立延迟/错误/重试/幂等/fence/事件 lag/投影拒绝 dashboards 与告警。
- [ ] **T051 [P]** 实现 retention/delete workflow、对象清理、短签 URL 与审计。（NFR-004/005）
- [ ] **T052** 性能测试 500 活动 WS、5 msg/s 突发和 NFR 延迟目标。（NFR-001/010）
- [ ] **T053** 执行 OpenAPI lint、单元/集成/契约/安全/并发/E2E 全量门禁，生成 FR/NFR 追踪矩阵。
- [ ] **T054** 故障演练：Redis 丢失、STT/TTS/LLM 超时、worker 中断、WS 重连和 PostgreSQL PITR。
