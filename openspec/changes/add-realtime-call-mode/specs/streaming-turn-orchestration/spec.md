## ADDED Requirements

### Requirement: Versioned streaming transcription and endpointing
系统 SHALL 持续产生带 call_id、turn_id 和 revision 的 partial/final 转写。依据后续 `fix-call-turn-taking` 修订，ASR endpoint只封存内部片段，业务轮次默认等待3000ms有效静音并完成独立校正后才提交；短停顿及内部最大长度切片不创建聊天消息。partial MUST 不触发实质回答、工具或事实记忆。

#### Scenario: Partial transcript is corrected
- **WHEN** partial 将数字识别为 fifteen，final 修正为 fifty
- **THEN** 只用 final 创建可回答问题，保留修订关联，不执行基于旧数字的查询

#### Scenario: Mid sentence pause
- **WHEN** Talker 句中短暂停顿且未满足完整轮次判定
- **THEN** 持续收音并更新同轮字幕，不因某个音频包结束而生成回答

### Requirement: Traceable question decomposition without universal review
系统 SHALL 将 committed turn 拆为有稳定 ID、source span、顺序、依赖、意图及置信度的问题；普通问题 MUST 自动路由，不默认 pending_review。无法可靠解析实体或结构 MUST 澄清或 hold。

#### Scenario: Independent safe and held questions
- **WHEN** 同轮包含“解释 DAU”和“查询昨天真实 DAU”
- **THEN** 两个问题分别保留，前者可自动短答，后者 hold，不猜测数据值

#### Scenario: Dependent question waits
- **WHEN** Talker 要求查询真实 DAU 并比较是否下跌
- **THEN** 查询与依赖其结果的比较一起等待授权，不用虚构值回答比较

#### Scenario: Ambiguous identifier
- **WHEN** final 的用户 ID 存在低置信度数字
- **THEN** 先英文澄清原 ID，不擅自改写或查询近似 ID

### Requirement: Idempotent turns and contextual repeats
系统 MUST 对相同 turn_id + transcript_revision 只提交一次；跨轮重复表达 SHALL 保留其重说/追问语义，并按最新策略和约束再评估。

#### Scenario: Duplicate final delivery
- **WHEN** ASR 重复发送同一 final
- **THEN** 问题、策略及回答不重复创建

#### Scenario: Caller asks to repeat
- **WHEN** 下一轮 Talker 说 “Can you say that again?”
- **THEN** 建立新对话轮次并重新校验重说内容，不被历史语义去重吞掉

### Requirement: Automatic concise English response
系统 SHALL 在有效 auto/notify 策略下按当前上下文、Wiki 和模型能力生成自然英文短答，默认 1–3 句、目标 20–60 词；寒暄/澄清 SHALL 按意图回应。MUST 不假装知道缺失的业务事实。

#### Scenario: Ordinary question without Hinter
- **WHEN** Talker 问普通常识且 Hinter 未操作
- **THEN** 无需人工确认即可经 Wiki gate 后收到英文语音和文本

#### Scenario: Chinese instruction changes content
- **WHEN** Hinter 指示“只说两句，数字还未核实，不要报数字”
- **THEN** 英文候选符合长度和事实边界，经校验才播出，不朗读中文提示

### Requirement: One output with per question scheduling
系统 SHALL 每 call 同时最多一个实质答复输出，按问题顺序及依赖调度；hold MUST 仅挂起该问题及依赖项。迟到的批准 MUST 在当前输出结束后重新核对相关性再调度。

#### Scenario: New question during review wait
- **WHEN** 数据查询处于 hold，Talker 接着问无关的普通问题
- **THEN** 持续接收并回答新问题，旧问题仍等待且不会突然插播

### Requirement: Cancelable Talker interruption
系统 SHALL 在服务端确认Talker有效插话时尽快本地停播、推进 response_epoch 并取消/丢弃旧生成和 TTS；浏览器RMS峰值不得单独决定取消。插话不等待三秒轮次结束或文本校正。历史 MUST 区分已播与未播，不自动续播已撤销尾部。细化策略见 `fix-call-turn-taking`。

#### Scenario: Caller interrupts mid answer
- **WHEN** Talker 在 AI 第二句开始前提出新问题
- **THEN** 清理旧播放和待发音频，后续上下文只将实际已播片段作为已告知内容，重新处理新轮

#### Scenario: Old worker completes late
- **WHEN** 已取消的 TTS/LLM 随后返回成功
- **THEN** epoch 校验拒绝其提交、播出和 completed 状态

### Requirement: Streaming output after final content gate
系统 MUST 缓冲每条完整短候选，通过最新 Wiki 校验后才公开文本或送入 TTS；SHALL 分句合成并连续发音频帧，不等整条答案音频文件完成。未经校验的 token MUST 不进入 Talker 或直接媒体输出。

#### Scenario: First verified sentence is synthesized
- **WHEN** 完整短答通过 Gate，首句音频已生成而后句尚在合成
- **THEN** 可以发送首句音频并继续收音，不等待整份音频落盘

#### Scenario: Framework tries direct output
- **WHEN** Pipecat 默认 LLM/TTS observer 产生未经许可的文本或音频
- **THEN** 桥接层阻断，Talker 不接收这些内容
