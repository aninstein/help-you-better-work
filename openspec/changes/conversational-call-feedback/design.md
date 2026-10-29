## Context

豆包公开材料支持自然全双工目标，但未公开内部“先说一句”算法。这里采用[一手调研](../../response-flow-research.md)中 LiveKit 的异步任务/空隙回应原则，结合本项目受限工具、自适应判停和两主状态，不声称复刻豆包。

## Decisions

### D1. 首次回应优先是有用内容

`ReplyAct` 有 `answer|acknowledgement|progress|clarification|limitation|refusal|supplement`，可在同一 response 中形成带 kind 的短段。简单问题直接答案即“先回应”；不机械地先 Okay 再回答。

发言权确认交接后，首个安全实质段若在约 250–400ms 的仲裁窗口内已准备，取消未发 acknowledgement；若确需慢工具或结果还未就绪，发一次自然短回应。不在用户未说完、纯噪声、附和或让 AI 等自己思考时抢话；不得为追求首声指标在 provisional 字幕阶段开启业务检索。

| 情形 | 英文示意，非固定必说 |
| --- | --- |
| 普通知识 | 直接答 “DAU means daily active users.” |
| 已接受的公开查询 | “Let me check that.”，进入实际工具执行后可说 “I'm checking the latest information.” |
| 实体不清 | “Which account do you mean?”，没有“正在查”的假承诺 |
| 无私有数据库能力 | “I can't access your internal metrics directly.”，有人工信息渠道再加 “I'll follow up when I have a verified figure.” |
| 明确禁止/不允许的请求 | 直接允许的边界答复，不先答应照办 |
| 工具失败 | “I couldn't verify that right now.”，可说明重试，不说查到了 |
| 原题晚到结果 | “About your earlier DAU question: …” |

电话产品继续英文输出，中文 Hint 影响内容但不让 AI 对 Talker 朗读中文。短回应用有限英文模板与状态驱动变体，或复用同次模型输出，不能为一句话再跑主 Agent/RAG。模板仅包含可审计意图，不插入私有 Hint、未经核实数字或敏感原问题。

### D2. “先回应再做事”的可执行顺序

```text
语义交接 + 确认真实交流意图
  → 最小策略裁决 + 最新适用约束
  → 直接短答案，或提交安全回应段
  → 回应首帧进入输出通道后启动已接受的慢工具任务
  → 工具真实开始/部分结果/完成事件
  → 可公开实质段或原题补答
```

不等回应整句播放完才开始工具。内部读取快照、选择路由和预热不是对用户承诺的“查询”，可以提前；公开检索和其他实际动作须有最终输入与允许参数。工具首先进入 accepted 状态，因此回应可以用将来意图 “Let me check”，不能在 running 前说“正在检索”。

回应 TTS 失败或客户端禁止播放时，以公开文字回应和可见语音错误降级，工具仍可继续，不能让工具永久卡在等扬声器。不能等待客户端播放 ACK 才执行只读查询；只需服务端已提交并送入有效输出通道。电话初始欢迎语不算本轮首次回应。

### D3. WorkItem 与正在说话解耦

`WorkItem(id, original_message_id, question_id, question_revision, need, state, attempt, deadline, cancel_policy, output_generation)`；状态 accepted/running/result_ready/failed/cancelled/expired。进度来自执行器，不来自模型想象。重复请求用 need/参数指纹和 revision 去重，不重复工具副作用。

默认任务只读、可取消；插话替换其原问题或用户说 stop/cancel 时取消任务，不能仅取消 UI 动画而让它继续插播。明确保留的独立问题/人工补答可继续后台准备，结果入队前重新读 Wiki，等用户自然停顿再补答。若问题被撤回、封禁或调用被撤销，迟到结果丢弃。

不增加写操作工具；未来若添加不可逆工具，需单独定义执行授权和取消语义，不能沿用本次允许公开查询的约定。

### D4. 进度回应按空隙和事实触发

同一个问题最多一次初始短回应。只有真实阶段变化或持续静默约 4–6 秒且任务仍有意义时才允许一次简短 progress；相同状态不得每几秒循环播报。拟定同一任务最多一次无新增事实的等待提示，之后到时明确失败/转后台并给有用选择，不能不断“still checking”。

若用户正在说、已有 substantive/supplement 可播、通话静音控制要求停止输出或旧任务已取消，则丢弃过期 filler；不是排队留到几秒后说。主输出优先级：显式停止/撤回 > 当前允许的澄清/边界 > 已就绪实质答案 > 有关联晚到结果 > 当前必要 ack > progress；首回应仲裁防止饥饿，但不能靠 progress 抢占已开播自然句。

多个问题的 ack 合并为一句对整段的回应，不五题各说收到；一个数据待补不应让其余四题排在“请稍等”后面。

### D5. 不让短回应重置十秒限制

原软件十秒预算继续从实际语义冻结开始，覆盖最终 ASR、按需校正、路由与首个有用结果的准备。Ack 不能重置这个计时器。若十秒内仍无实质结果，必须明确当前限制、失败或已接受的后台处理状态，并让前台恢复倾听；不是超时就转人工。

后台只读任务使用独立 wall deadline，拟定默认 30 秒；超时终止并给明确结果，不无限延长。若当前电话里用户持续发言不便播放，结果可保持可见/可补答，不自动抢话；排队等待与工具耗时单列，不能用排队暂停隐藏真实迟到。挂断取消 call 临时任务，不宣称挂断后自动通知，除非已有明确持久待补渠道。

### D6. 两种主状态与活动提示

`primary_activity=listening|thinking` 延续现有语义；`playback=idle|speaking` 和 `pending_work_count` 独立。语义冻结进入 thinking；有意义新语音打断前台思考后进入 listening 并收集，不因后台独立任务仍运行保持“思考中”。

播音时仍采集。确认插话撤销相关 output generation、丢队列并转倾听；短附和仍遵从既有策略。纯 stop 不触发新的 Okay；Hinter 暂停/封禁直接生效。继续使用现有圈和音频线，倾听边缘随真实音量变化，思考呼吸动画，不新增一套聊天页面或新的确认弹窗。

### D7. 目标与观测

以下为拟定体验目标，非已测承诺：暖机普通完整短问，从最后有效语音到首个可听回应争取 ≤1.5 秒，到首个实质音频争取 ≤3 秒；慢查询可先回应，实质结果另计。最终 ASR/本地 TTS 现状可能无法达到，必须按阶段测后优化，不通过更换计时起点宣称成功。

记录 `voice_end → semantic_freeze → transcript_final → route → first_reply_commit → first_audio_sent → client_play_started → first_substantive_play_started → task_done`。实际播放由客户端音频时钟确认，不用服务端发送冒充已听；故障、冷启动、人类等待分组列出。最少看首次回应、首个实质结果、重复 filler 数、超时数，不堆监控平台。

## Trade-offs

先说一句会占用约一秒声道，快答案往往直接说更好；模板缓存减少首声等待，但仍要检查当下允许表达。自然回应只能改善感知，不能替代缩短模型和 TTS 路径。若无可用音频，只能诚实显示降级，不能用动画冒充交流顺畅。

## Verification

以常识直答、慢公开查询、查询失败、无私有数据源、用户在 ack/思考/播音时插话、一段五问和挂断晚结果各走一次代表流。确认 ack 不重复、无虚构工具动作、十秒不重置；复用 D 的段级取消验证。
