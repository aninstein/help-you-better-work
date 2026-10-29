## ADDED Requirements

### Requirement: Live supervision and actual delivery timeline
Hinter 页面 SHALL 实时呈现问题、转写修订、策略动作、AI 答复与实际播出状态，明确区分候选、已校验、正在播、已播完、中断与失败。普通问题 MUST 无需人工点确认。

#### Scenario: Observe ordinary automatic calls
- **WHEN** AI 自动完成普通问答
- **THEN** 问题与英文答复按有序事件滚动显示，已播状态来自播放确认，不将仅生成完成显示成已听到

#### Scenario: Reading history
- **WHEN** Hinter 上翻查看历史时出现新回答
- **THEN** 不强制滚动到末尾，显示新增计数并提供回到最新

### Requirement: Actionable aggregated alerts
系统 SHALL 按 question/version/rule 聚合提醒，hold/deny 进入醒目待办；notify 默认仅角标。提醒 MUST 展示原因、相关问题、等待时间和可用操作，重复事件不得重复响铃。浏览器通知 SHALL 遵循现有授权状态。

#### Scenario: Multiple partial risk matches
- **WHEN** partial 反复命中且 final 提交一次风险问题
- **THEN** partial 只更新预警，final 创建一个正式待办，不逐帧弹窗或响铃

#### Scenario: Hinter returns after disconnect
- **WHEN** Hinter 重连并恢复游标
- **THEN** 展示未处理/已过期提醒和已回答历史，不重放全部旧提示音

### Requirement: Untargeted and targeted Chinese intervention
Hinter SHALL 在任何时刻提交中文提示，不强制选择问题；系统 MUST 在 ack 明示实际 target、scope 和状态。默认当前 active response/turn，无活动时作用于下一轮；默认 scope 为本 call。

#### Scenario: Short numerical hint
- **WHEN** 当前问题明确问 DAU，Hinter 输入“1234”
- **THEN** 提示绑定该问题，存原文及绑定；数字不会自动应用于其他 ID/指标，语义有歧义时请求澄清

#### Scenario: Hint before caller speaks
- **WHEN** listening 状态收到“接下来只回答两句话”
- **THEN** 存为 call 级约束并在下一轮生成与最终 Gate 中使用

### Requirement: Immediate stop and correction
Hinter SHALL 能发“立即纠正”“别回答”“暂停自动回答”；服务端 MUST 优先撤销 permit、推进 epoch 并停播，不等待模型解释或 Wiki 文件渲染。补充提示默认立即纠正当前回答，亦提供明确的“仅后续生效”动作。

#### Scenario: Do not answer during playback
- **WHEN** AI 已开始播放，Hinter 发“别回答”
- **THEN** 终止旧输出并记录已播范围，当前问题被阻断，后续旧音频不得恢复

#### Scenario: Correction arrives before final gate
- **WHEN** 候选正在检查，Hinter 纠正事实
- **THEN** 旧 check 作废，持久化新约束后重生成/重验，不发旧答案

#### Scenario: Stop persistence fails
- **WHEN** stop 已到达但约束持久化失败
- **THEN** 仍保持停播/暂停，显示保存失败与重试，不自动继续旧回答

### Requirement: Explicit pause resume and review actions
Hinter SHALL 能暂停/恢复自动回答，并对 hold 执行批准一次、补充后批准、拒绝或延后；操作 MUST 校验 question_version、constraint_revision 和幂等 key。恢复不得复活取消输出或越过 deny。

#### Scenario: Resume after emergency pause
- **WHEN** Hinter 明确恢复自动模式
- **THEN** 仅未来经新策略/约束校验的输出可继续，不播回暂停前的缓存音频

#### Scenario: Stale approval
- **WHEN** 同时发生问题修订与人工批准
- **THEN** 旧版本批准返回 VERSION_CONFLICT，不能批准更新后的实体或答案

### Requirement: Private text channel and silent observer
Hinter 提示、审核细节和内部 Wiki SHALL 仅 Hinter 与内部处理可见；Talker 的 HTTP/WS/SSE/RTVI/音轨/错误 MUST 不含私有内容。Hinter 页面默认不采集语音且不自动播放通话音频。

#### Scenario: Private canary instruction
- **WHEN** 提示含唯一 canary，并用于限制回答
- **THEN** Talker 只收到符合约束的英文回答，任何可见字段或音轨不含 canary/提示原文/审阅身份

#### Scenario: Two tabs on one computer
- **WHEN** 同一电脑同时打开 Talker 与 Hinter
- **THEN** call 中默认只有 Talker 播放，Hinter 可监管文字而不会重复扬声器输出
