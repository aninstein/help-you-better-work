## ADDED Requirements

### Requirement: Actual incremental model consumption
系统 SHALL 在模型未完成整答时消费和提交允许的完整短句，工具分支 MUST 不把无工具正文整轮缓存到结束才 yield；原始工具参数和推理 MUST 不进入公开文本。

#### Scenario: Long answer before model completion
- **WHEN** 模型持续生成多句英文解释
- **THEN** 第一段提交、合成和开始播放发生在模型整答完成前，而不是完成后切段

### Requirement: Shared public stream across modes
文本、上传语音和电话 SHALL 使用共用 ResponseStream 公开已验证短句，无需人工确认。系统 MUST 区分生成结束、文本结束、发送结束和实际播放结束，整答文件归档不得阻塞首声。

#### Scenario: Uploaded voice receives an answer
- **WHEN** 上传语音转写完成并开始自动回答
- **THEN** 同一气泡不断追加已提交正文，允许逐段播放，不等完整下载音频文件

### Requirement: Coherent bounded speech aggregation
系统 SHALL 优先按完整句聚合增量正文，将过短开头与紧随其后的解释组成自然语音单元，保持否定、条件、数值、ID 和事实限定完整；不得为凑短首句或固定词数在逗号处切断。每段 MUST 使用最新适用 Wiki 验证后才公开和合成。

#### Scenario: Sensitive fact with a qualification
- **WHEN** 一个事实的限定条件尚未生成完整
- **THEN** 聚合器暂不公开不完整断言，不先说错误前半句再用后段修正

#### Scenario: Short recommendation with an explanation
- **WHEN** 模型先生成很短的推荐结论，随后给出简短理由
- **THEN** 可以合为一个有完整语气的语音单元；简短整答在生成结束即提交，不为凑字额外等待

#### Scenario: Continuous speech across sentence boundaries
- **WHEN** 前一句仍在播放且后一句正文已经可用
- **THEN** 有界预合成下一句，共用连续发送/播放时钟，不先等前一句播完才合成；正常相邻回复不截掉已排队的句尾，明确打断与撤回仍立即清理声音

### Requirement: Honest TTS capabilities and bounded queues
系统 SHALL 声明实际 TTS 流输入、音频流和取消能力；本地短句降级不得冒称原生双向。候选、合成与播放队列 MUST 有界，媒体/控制接收不得被 native 合成锁阻塞。

#### Scenario: Native synthesis cannot be cancelled immediately
- **WHEN** 本地 native 推理仍占用模型而用户打断
- **THEN** 输入和控制继续处理，旧任务完成后丢结果且保持模型锁安全，不把取消 async task 当 native 已退出

### Requirement: Full-path interruption and latest-guidance invalidation
有效插话、停止、撤回、模式切换与挂断 SHALL 撤销相关输出所有权，取消模型/聚合/检查/TTS/音频队列；所有异步返回 MUST 验证 generation 和最新适用版本。持续麦克风不得因输出流而暂停。

#### Scenario: Interruption while thinking or speaking
- **WHEN** Talker 在推理、检查、合成或播放任一阶段发出确定的有效新语音
- **THEN** 系统转倾听收集新表达，相关旧段和迟到帧不再播放，独立人工补答按原题保留

#### Scenario: Hint changes before a segment is published
- **WHEN** 新禁令已持久生效而旧段尚未提交
- **THEN** 旧段丢弃，按新 Wiki 重建或安全结束，不要求 Hinter 再次逐句批准

### Requirement: No rewind of already public content
首段公开后系统 MUST 不用整答 reset 或从头备用模型输出覆盖已公开/已播内容；失败 SHALL 保留允许的前缀并明确部分失败或关联续答。

#### Scenario: Model fails after two spoken sentences
- **WHEN** 模型中途报错
- **THEN** 不从第一句重新生成朗读，已说部分保留，剩余部分明确失败或用新关联回复续答

### Requirement: Segment identity and real playout evidence
音频 SHALL 携带可验证的 response/generation/segment/frame 身份，客户端 MUST 丢弃过期、撤回和损坏帧；已播前缀 MUST 基于实际音频播放确认，无词级对齐时不猜测半句精确文字。

#### Scenario: Delayed old audio after cancellation
- **WHEN** 新 generation 开始后收到旧响应帧或 ACK
- **THEN** 不播放、不复活旧文本，也不标成新响应已播

### Requirement: Reconnection and withdrawal consistency
重连 SHALL 先恢复公开段与 tombstone，MUST 不默认重播全部历史声音；withdrawn 内容在文本、音频、快照和 Wiki 中持续隐藏。未播候选不得记为已告知事实。

#### Scenario: Disconnect during a sentence
- **WHEN** 电话在半句播放时断线后重连
- **THEN** 保留允许的公开状态并标实际交付未知或部分，不自动从头朗读或声称整句已听

### Requirement: Independent latency accounting
系统 SHALL 区分首回应、首个实质段、模型结束、合成首包与实际播放，持续记录停顿与取消迟到；acknowledgement MUST 不重置前台处理截止或计作实质结果。

#### Scenario: Long speech with quick first sentence
- **WHEN** 答案持续生成和播放十秒以上
- **THEN** 记录第一实质段的真实时间，十秒前台边界不被误用为整段音频长度限制，流停滞仍有独立超时处理
