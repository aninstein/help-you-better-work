## ADDED Requirements

### Requirement: Continuous media with one buffered conversational turn
系统 SHALL 在实时通话中持续接收 PCM、持续更新同一个临时字幕槽，并将 ASR 内部片段聚合为稳定 turn。音频包、partial、ASR endpoint 或 ASR 最大长度切片 MUST 不直接创建 Talker 消息、问题或主 Agent 调用。

#### Scenario: Multiple acoustic endpoints in one thought
- **WHEN** 用户一句话含多个短停顿，ASR先后输出多个segment final
- **THEN** 系统按segment顺序保留文本，partial只替换当前segment修订，最终同轮仍只形成一个输入气泡

#### Scenario: ASR rolls over during long speech
- **WHEN** 用户持续说话超过ASR内部18秒切片长度
- **THEN** 内部识别流可以重置，但业务turn持续累积，不因长度规则提前回答或丢失前段内容

### Requirement: Three second effective silence commits a turn
系统 MUST 默认从最后一帧有效语音结束起等待3000ms连续有效静音，再冻结轮次供最终识别和校正；新有效语音 SHALL 重置静音窗口。三秒不是最短发言长度或周期上传间隔；计时 MUST 使用同一音频时间基准，不能用ASR输出延迟或网络断包代替静音。

#### Scenario: Two second thinking pause
- **WHEN** 用户说 “Could you compare”，停顿2秒，再说 “these two options?”
- **THEN** 系统持续收音，不在停顿期间回答，最终将两段合成一个完整输入

#### Scenario: Short greeting followed by silence
- **WHEN** 用户只说 “Hello” 后持续静音约3秒
- **THEN** 系统封存一个有效轮次，不要求用户先说够3秒

#### Scenario: Stream stalls or microphone is muted
- **WHEN** 未提交的用户发言期间网络断流或用户开启静音
- **THEN** 系统暂停/终止该缓冲并给出明确状态，不把无数据视为3秒静音后自动回答；用户静音后缓冲不自动提交

### Requirement: Revision safe refinement before single message submission
系统 SHALL 区分冻结、校正与正式commit，只有最新turn revision校正完成或明确降级后才提交一条Talker消息并调用一次主Agent。重复final、重复完成事件和迟到任务 MUST 不重复创建气泡或问题。

#### Scenario: User resumes during correction
- **WHEN** 三秒后文本正在校正，用户继续补充且上一轮尚未commit
- **THEN** 续说并入同一turn的新revision，旧校正结果作废，重新等待三秒；历史里不出现两条碎片消息

#### Scenario: User resumes after commit
- **WHEN** 上一轮已正式提交后用户再次发言
- **THEN** 开启下一轮并保留上一条原话；按有效插话策略取消过时输出，不能将新话追加改写到已提交气泡

#### Scenario: Duplicate completion arrives
- **WHEN** 同一call/turn/revision的ASR或校正结果被重复投递
- **THEN** 只保留一次提交与一次主Agent触发

### Requirement: Bounded capture without silent truncation
系统 SHALL 为单轮音频设置明确上限，初始为60秒；达到上限必须保留已采集内容并提示用户短暂停顿或重说，停止扩大该缓冲。系统 MUST 不在用户未知情时截断原话后把残句提交为正常问题。

#### Scenario: User speaks beyond the input limit
- **WHEN** 当前轮次已到音频缓冲上限而用户仍持续讲话
- **THEN** 界面明确提示输入未完成/需暂停，已有原声和原文保持关联，不自动发布基于截断内容的回答

### Requirement: Existing review and answer paths remain distinct
三秒策略 SHALL 仅作用于通话语音输入；旧文字/录音上传保留原交互。完整用户turn提交后，系统 SHALL 继续由主Agent拆题、读取最新Hint/Wiki，答案经现有校验后再分句连续TTS；MUST 不把ASR分片或未经校验的输出token直接当公开答案。

#### Scenario: One turn contains two questions
- **WHEN** 用户同轮问两个独立问题后停顿3秒
- **THEN** 时间线只有一个Talker输入，主Agent可拆为两个问题并按现有策略回答或待提示

#### Scenario: Caller uploads an ordinary recording
- **WHEN** 用户在非通话的语音消息模式上传录音
- **THEN** 按旧上传处理，不额外等待通话三秒或要求进入通话
