# 通用 Agent 与记录接口契约（目标）

本文定义产品接入层契约，不假定字段是 DeerFlow 官方接口。适配器负责映射固定版本的运行时接口。

## 身份与资源

- 所有接口除登录/注册等公开入口外均要求有效登录会话。
- 普通用户的 `owner_user_id` 从服务端会话确定；Hinter 访问其他用户时显式选择目标，并校验 `workspace.supervise` 权限。
- `actor_user_id` 始终为登录账户；模型和客户端不能覆盖它。
- `thread_id`、`run_id`、记录、音频和 artifact 的访问均校验实际 owner。

## 会话与 Agent 信息

| 接口 | 用途 |
| --- | --- |
| `GET /api/v1/agents` | 返回允许使用的 Agent 名称、简介、工作配置和能力 |
| `POST /api/v1/threads` | 建立当前用户的个人 Agent 会话 |
| `GET /api/v1/threads/{id}` | 获取会话、记录游标与当前 Run 状态 |
| `GET /api/v1/threads/{id}/records?after_seq=` | 按序读取有权限的通用记录 |
| `POST /api/v1/threads/{id}/inputs` | 持久化文本/已确认转写并创建 Run |
| `GET /api/v1/threads/{id}/projections?mode=questions` | 读取问题形式投影 |
| `POST /api/v1/runs/{id}/cancel` | 请求停止指定 Run，不删除记录 |
| `GET /api/v1/runs/{id}/events?after_seq=` | 读取/订阅可重连的运行事件 |

输入样例：

```json
{
  "agent_id": "personal-employee",
  "profile": "meeting_assistant",
  "input": {
    "kind": "text",
    "text": "先记录：试点周五结束。我们还有哪些事项没确认？",
    "source_record_id": null
  },
  "presentation_mode": "questions"
}
```

请求通过 `Idempotency-Key` 去重。服务端先提交 record 和 Run，再返回 `202`：

```json
{
  "record_id": "record_...",
  "thread_id": "thread_...",
  "run_id": "run_...",
  "status": "queued",
  "presentation_mode": "questions"
}
```

`presentation_mode` 不选择模型或执行分支。`summary` 为后续保留模式；一期请求它时返回 `422 PRESENTATION_MODE_UNSUPPORTED` 和当前支持列表。

## Run 与事件

状态：`queued → running → completed | failed | cancelled`，需要用户输入时可进入 `waiting_input`，收到明确关联的补充后回到队列。进程故障恢复使用同一 `run_id` 和递增 `attempt`，不改写为一个无关联的新问题。

统一事件信封：

```json
{
  "event_id": "event_...",
  "thread_id": "thread_...",
  "run_id": "run_...",
  "seq": 12,
  "type": "assistant.delta",
  "source_record_ids": ["record_..."],
  "payload": {"text": "还需要确认负责人和验收范围。"}
}
```

事件类型包括 `run.queued`、`run.started`、`tool.started`、`tool.completed`、`tool.failed`、`assistant.delta`、`run.waiting_input`、`run.completed`、`run.failed`、`run.cancelled`、`projection.updated`。工具执行只在收到真实完成结果后标记成功。订阅断开不取消 Run；`seq` 用于幂等重放，不暴露内部推理内容和私有管理员输入。

## 记录修订与问题投影

- 原始记录持有 `revision`；纠正创建修订及审计关联。
- 投影包含 `projection_id`、`kind=questions`、`source_record_ids`、`source_revisions`、`items`、`status`。
- 每个问题 item 保留来源范围、文本与可选 `related_run_id`。投影错误显示提取失败，原记录与主 Run 保留。
- 未来摘要投影使用同一来源契约，不要求重新存储一套聊天记录。

## 错误与旧接口

`401` 表示需登录，`403` 表示无操作权限，资源不属于当前用户时按统一策略返回 `404`；`409` 表示版本或幂等冲突；`503 AGENT_RUNTIME_UNAVAILABLE` 表示运行时不可用。已受理 Run 的执行错误通过 Run 事件返回。

旧 `/sessions`、`/messages`、`/questions` 可在迁移期作为兼容适配，必须执行新认证/所有权检查并转入同一 DeerFlow Run。旧 `/auth/demo` 和请求参数选角色的行为由配套 change 移除。

## Hinter 协作恢复补充（2026-10-26）

主 Agent 通过 DeerFlow 原生 `request_hinter(summary, needed_information, reason)` 工具登记持久化求助，`reason` 为 `internal_data | uncertain_facts | disclosure_needed`。同一 Run 只登记一次。来源必须为当前所有者可见记录及其有效修订；工具本身不能批准披露或读取管理员私有备注。普通问题不强制送审。

| 接口 | 权限与作用 |
| --- | --- |
| `GET /api/v1/admin/assistance-requests?page=&page_size=` | `workspace.supervise`，分页读取待补充事项与总数 |
| `GET /api/v1/threads/{id}/assistance-requests` | 会话所有者/监管者，普通用户只读精简状态 |
| `POST /api/v1/assistance-requests/{id}/resolve` | `guidance.write`，`action=provide/decline/retry`、`text`、`expected_version`，需要 `Idempotency-Key` |

求助状态 `pending → provided | declined | superseded`。待办存在 MySQL schema v6 的独立表中；Run 保持现有队列生命周期，登记求助后仍可先给已知信息并完成当前阶段，避免同一会话和其他任务被人工等待阻塞。Hinter 明确提交允许披露的补充是新的关联输入：事务中写公开提示、更新求助并创建带 `parent_run_id` 的续答 Run，模型收到原问题与该公开提示后继续回答。该新 Run 不是将进程失败伪装为无关联的新问题；崩溃恢复仍沿用同一 Run/attempt/checkpoint。

Hinter 私有备注不进入续答输入。暂不提供不披露任何私有理由。原问题修订后旧待办失效。重复处理、请求结果不明重试、多个 Hinter 并发都使用版本与幂等命令保护；续答失败时 `retry` 复用原公开记录，避免重复披露或重复回复。
