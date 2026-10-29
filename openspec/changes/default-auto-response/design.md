## Context

`pending_review` 同时承担了问题入库、风险通知和实际人工等待，`ai_mode` 仅自动建草稿；通话另有宽泛分类。必须统一决策语义，而非只把 actions 改成 auto 后让不可靠数字直接流出。源码依据见[调研](../../response-flow-research.md)。

## Goals / Non-Goals

让普通输入无人工点击即可获得答案；让人工待补原因少且明确；保留问题、来源和干预能力。此次不添加真实业务数据库连接、任意 shell/MCP、支付或账户写操作，不建设审批平台或大量策略题库。

## Decisions

### D1. 入口与回答模式解耦

`input_mode=text|voice_message|realtime_call` 只决定采集和输出表现；`response_policy=automatic` 为新 session 与新输入默认行为。旧 `review_chat` 可保留接口枚举，但不再代表人工放行。`ai_mode` 旧开关停止控制新自动回答；UI 改为“自动回复运行中”，保留显式暂停/恢复作为人工控制，不能默认暂停。

输入收到即分配稳定 message ID 与 provisional question ID。普通单问无需先等整段“审核总结”才启动回答；拆题只决定依赖与路由，不同时生成完整答案。多问在轻量拆题完成后可独立处理；较长表达超过一批五题时保留原文和后续批次，不能截掉第六题。问题整理/去重可后台补充，但风险裁决必须在该内容首次公开之前完成，不能先泄露后分类。

不需要额外工具时，路由与正文生成可以是同一流式调用的控制头和正文两阶段：控制头未校验不可对外；生成 tool_call 则交任务执行器，不能把工具参数当答案。模型不稳定支持此格式时采用短小独立路由调用；不要恢复含完整答案的 JSON 规划。

VoiceAgent 的 “human-approved / private draft” Prompt 同步改为自动公开回答契约，不能绕过 API 确认后仍让模型自称只写草稿。当前 ToolRuntime 默认包含 bash 和可配置 MCP，自动流不能照搬：三入口共用静态 `automatic_readonly` 工具配置，仅注册已允许的公开搜索、受控页面/用户明确文档读取、计算与受身份过滤的知识读取；任意 bash、未声明 MCP、外部写入不暴露给自动模型。已有后台 Wiki 写入由确定性记忆/Hint 服务执行，不算模型任意写文件。保留原管理扩展配置，但不因取消答案审核而自动扩大其执行范围，也不新增工具审批界面。

### D2. 类别不是裁决，词表只提供信号

引入逐题 `Decision(action, reason_code, matched_rule_ids, policy_revision, needs, evidence_scope)`；`action` 为 `answer|lookup|clarify|refuse|limited_answer|await_human`。主题类别用于观察，不直接决定 hold。

| 例子 | 默认动作 | 人工待补 |
| --- | --- | --- |
| 问候、常识、人生意义、普通主观问题 | 简短直接答，主观观点保留不确定性 | 无 |
| “What does DAU mean?” | 解释定义 | 无 |
| 明确要求搜索某公开新闻/天气 | 有工具就公开查询，无工具则告知不能验证当前情况 | 无 |
| 听不清 ID / “查那个数据” | 针对缺失槽位问 Talker 一个简短问题 | 无 |
| “昨天我们公司的 DAU 是多少”但没有授权数据源 | 立即说明无法核验；若可由 Hinter 提供则创建原题待补 | 仅等待提供真实依据，不是审稿 |
| 查询真实账户持有人/非公开记录 | 不公开检索，不臆造；有已授权依据且允许披露才能答，否则限制答复或等待指定人工依据 | 只在确有人工信息来源且可合法披露时 |
| 明确禁止某主题/某身份信息的有效 Hint | 按禁令给允许的边界回复，不重问许可 | 无 |
| 明确“报价格前必须由我确认” | 其他内容可答，报价子问题等待 | 匹配该有效规则时 |
| 违法暴力的可操作伤害协助 | 安全拒绝/安全转向 | 无，人工不能解禁 |
| 防范诈骗、文学讨论、kill a process | 根据真实语境答 | 无 |
| 工具失败/模型超时/未知类别 | 限制答复、明确失败或澄清；保留可重试任务 | 无自动转人工 |

“无法核验”是已经给出的有效回应，不把未提供数字标成已完成数据查询。若没有接单的 Hinter 或资料来源，则结束为 unavailable，不永久 pending。未知分类不得落入 ambiguous→hold，禁止把任意敏感词正则当最终伤害判决。

### D3. 人工等待只允许显式、可解释的入口

允许 `human_confirmation_required`（有效且精确匹配的人工规则）、`human_evidence_required`（真实非公开数据尚缺且已有指定人工提供渠道）、`constraint_conflict`（适用人工约束真实冲突，无法选择安全替代）三个 reason。每个 await_human 必须带 target、缺失信息、来源、解决动作及 `blocking_scope=question`；不阻塞整轮和其他题。

对必须拒答的禁止项，不进入等批准状态；对语义不清不默认请 Hinter 猜。重叠规则采用安全禁止优先，人工确认不覆盖禁止。轻微脏话可作观察记录，无需给 Hinter 弹警。实际应审核占比通过可见 reason 观察，不能靠随意降低阈值硬凑比例。

### D4. 问题、生成和交付状态分开

```text
Question: recorded → ready | awaiting_user | awaiting_evidence | awaiting_human
ready → responding → resolved | partially_resolved | unavailable
任意状态 → interrupted | blocked | withdrawn
Response: planned → streaming → completed | interrupted | failed | withdrawn
ReviewNotice: informational | action_required → resolved | dismissed
```

`awaiting_evidence` 内区分 `provider=tool|human`；Hinter 待补面板只显示 human 和 awaiting_human，不把运行中的工具算人工待办。`human_reviewed` 只陈述真实人工处理事实，自动答案为 false，但可正常公开。安全拒绝可将请求处理为 resolved（outcome=refused），不是 pending。

同题并发有唯一 active generation 和 revision；消息可对应多题，答案绑定实际 question IDs。独立题不互相等待；依赖子题只等必要证据，不以人工待办数锁住整批。人工补充产生关联原题的 supplement，不冒充当前新题答案。

### D5. Hinter 页面从审核台改为可干预的对话观察

主视图持续滚动已发布/正在发布 AI 气泡、对应问题和结果，支持任意气泡手动撤回；默认不显示成排“确认发送”。待补区仅 action_required，显示缺什么、为何只有人工可补，不用红点表示每条普通问题。提示入口可不选问题，默认 session 作用域；指定问题/客户作用域显式选择，详见 B。

文本和上传语音即时显示同一条流式 AI 气泡，普通答案不隐藏在私有草稿区。流式时仍能中止/发 Hint；通话使用现有动画和紧凑控件，不增加占屏审核表单。

### D6. 迁移与模式切换

引入 `response_flow_version=2`，既有 completed、withdrawn、人工来源不变。历史 pending_confirmation 草稿保持私有，允许手动发送或丢弃；迁移不偷偷对 Talker 发布。历史普通 pending_review 显示为历史未处理，不批量自动补答；用户再次询问或 Hinter 显式重试才启用新路径。新输入立即使用自动流，不受遗留草稿阻挡。

开始通话时，已有公开流在明确边界被中断并留存已公开部分；未发布草稿留存私有，不要求先审核草稿才能拨号。取消跨模式活动输出并更新 ownership generation，最多一个自动语音输出；旧任务迟到不可发布到新模式。挂断取消电话临时输出，不删除持久人工待补；后续在消息模式补答必须明确标记来源且不能自动播放旧通话声音。

### D7. 失败策略

记录失败重试 ID，向用户给可重试的简短说明；只在确实创建并接受了后台任务时承诺稍后补充。任务迟到须重读 B 的约束与问题 revision，已撤回/取消的不再恢复。Hinter 暂停属于显式控制，不与“默认自动”矛盾；恢复只重新处理仍有效的任务，避免把积压题全部朗读。

## Risks / Trade-offs

自动发布减少逐字人工把关，依赖明确的约束读取与自动短句检查；不能用移除 Wiki 校验换速度。过宽人工规则仍会造成高等待率，因此面板应展示命中规则而非隐蔽阈值。数据缺失可能只能得到诚实限制说明，这优于编造答案。

## Verification

少量真实流：三个入口普通问无需确认；哲学/澄清/失败不人工等待；混合五问仅真实数据待补且普通题先答；显式禁止直接遵守；旧草稿不泄露；模式切换不双播。无需新题库或新审批门禁。
