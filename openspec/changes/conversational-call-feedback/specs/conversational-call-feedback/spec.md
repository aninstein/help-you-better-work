## ADDED Requirements

### Requirement: Immediate useful response before slow work
电话系统 SHALL 在确认语义交接后尽快给允许的有用声音回应；直接可答时直接回答，慢任务先提交自然短回应再执行，不等待整个答案。系统 MUST 不在未完成表达或无交互意图时抢话。

#### Scenario: Fast direct answer
- **WHEN** 普通常识首段已就绪
- **THEN** 直接播放实质答案，丢弃尚未发出的多余“收到”

#### Scenario: Slow accepted lookup
- **WHEN** 最终输入要求可执行的公开查询
- **THEN** 先将允许的简短回应送入输出通道，再启动工具，不等待整句播放或整答完成

### Requirement: Honest task speech
回应 MUST 反映已接受、执行中、完成、失败和无能力等真实状态；不得虚构工具、进展、人工接单或已核实结果。回应 SHALL 使用英文并遵守最新 Wiki。

#### Scenario: No internal database access
- **WHEN** Talker 要求真实内部指标但没有授权数据源
- **THEN** AI 说明不能直接核验，仅在确有后续信息渠道时承诺补充，不说正在查库

### Requirement: Nonblocking cancellable work
慢任务 SHALL 与前台说话和倾听独立，保存原题及取消策略；有效插话、撤回与封禁 MUST 阻止已取消任务迟到插播。独立已保留任务 SHALL 可在重新校验后关联原题补答。

#### Scenario: New request while earlier evidence is pending
- **WHEN** Talker 提出新问题且旧题为独立保留任务
- **THEN** 系统倾听并回答新题，旧结果在自然空隙按原题补答，不能阻塞新题或混用问题编号

### Requirement: Sparse progress without filler backlog
系统 SHALL 仅在实际需要、持续空闲及真实任务仍运行时给有限进度回应；多个问题合并回应，过期 filler MUST 丢弃，不得排队挤占实质答案。

#### Scenario: Result becomes ready before filler plays
- **WHEN** 已计划等待提示但实质结果先就绪
- **THEN** 取消提示并说结果，不先朗读过期“正在查询”

### Requirement: Time-bounded foreground and background work
首次短回应 MUST 不重置语义冻结起十秒前台处理预算；超时 SHALL 给明确限制或已接受后台状态并恢复倾听，不自动转人工。后台任务 MUST 有独立有界截止。

#### Scenario: Acknowledgement followed by timeout
- **WHEN** AI 已说收到但十秒内仍未取得实质结果
- **THEN** 系统明确当前处理结果/限制，保留或结束实际任务，不继续无声思考或重复口头禅

### Requirement: Continuous listening and state consistency
系统 SHALL 保持麦克风采集，确认有意义插话后从前台思考/播放转倾听并收集新文字；后台独立任务 MUST 不把前台强制锁在思考状态。

#### Scenario: Interrupt the first spoken acknowledgement
- **WHEN** Talker 在首句回应期间发出真实新请求
- **THEN** 相关旧回应和输出任务停止，系统收集新输入，不在之后补播旧 acknowledgement

### Requirement: Distinct response and result latency evidence
系统 SHALL 分别记录首声、首个实质结果、任务总耗时与实际播放开始，不将服务端首包当客户端已听，也不以欢迎语或 filler 计作实质答案。

#### Scenario: Fast filler and slow result
- **WHEN** 0.8 秒说出短回应但 8 秒后才开始说查询结果
- **THEN** 两个延迟分开显示或记录，不声称答案在 0.8 秒已交付
