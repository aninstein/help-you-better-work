## ADDED Requirements

### Requirement: Separate interruption intent from speech presence
系统 SHALL 独立判断附和、预期确认、真实中断和不确定声音。人声时长与新词只能作为证据，MUST 不单靠持续800ms或音量变化撤销AI输出；结束发言模型不得被当作主说话人或插话意图识别器。

#### Scenario: A call connects in listening mode
- **WHEN** 用户点击电话按钮且媒体连接完成
- **THEN** 默认进入倾听状态，持续采集语音并更新文字字幕，确认本段说完后才进入思考

#### Scenario: Recognized speech interrupts thinking
- **WHEN** AI正在拆题、生成或校验，用户新语音已识别出有效文字，包括一个单词的补充
- **THEN** 取消当前被打断的思考任务，回到倾听并继续累计语音转写，不等待完整新句或停顿；旧结果不能迟到播出，独立待补答仍保留

#### Scenario: Sound without recognized words occurs during thinking
- **WHEN** 麦克风检测到声音但尚无有效文字内容
- **THEN** 不只凭声音或时长确认打断当前业务思考

#### Scenario: Caller gives a backchannel during an explanation
- **WHEN** AI讲解时用户短促说 “uh-huh” 或 “yeah”，没有纠正或新请求
- **THEN** 当前解释继续，不新建无意义问题，不硬取消当前回答

#### Scenario: A short response answers a confirmation request
- **WHEN** AI正在结束一个确认问题，用户说 “No”
- **THEN** 该短回应作为有效输入保留，不能因它只有一个词而当背景附和丢弃

#### Scenario: An acknowledgement turns into a correction
- **WHEN** 用户说 “Yeah, but that number is wrong…”
- **THEN** 后续纠正能升级为真实插话，不能因前缀被识别为附和而丢掉整段表达

### Requirement: Explicit interruption has a fast path
当前有效人声包含明确的stop/wait或真实纠正/新请求时，系统 SHALL 尽快让出发言权。Hinter显式控制、撤回和挂断 MUST 沿用立即停止路径，不等待端点模型、三秒静音、文本校正或主Agent。

#### Scenario: A short stop is spoken
- **WHEN** 用户对正在讲话的AI清楚说 “Stop”
- **THEN** 不要求凑够多词或三秒，及时停止旧输出并保持采集；记录从声音开始到确认及实际停播的完整耗时

#### Scenario: Supervisor blocks an answer
- **WHEN** Hinter点击别回答、撤回或发送生效的封禁原则
- **THEN** 输出控制优先执行，候选插话恢复事件不能重新播放被撤销的回复

### Requirement: Uncertain interruption can recover without cancelling an answer
系统 SHALL 为疑似插话提供有界的候选降音阶段；降音MUST绑定response/epoch/candidate，并保持持续收音。未确认前不得推进epoch、清空历史或丢掉当前答复；误候选应恢复原音量。确认后才取消旧输出，MUST 不自动重播正式取消的尾句。

#### Scenario: A short sound is mistaken for a speech start
- **WHEN** 候选阶段随后被判断为附和或非交互噪声
- **THEN** 恢复仍有效的当前输出，不产生撤销、重复播报或新问题气泡

#### Scenario: A restore event arrives after a real interruption
- **WHEN** 新输出epoch已生效，旧候选的restore迟到
- **THEN** 旧事件无效，已停止的答案不会恢复

#### Scenario: Classifier remains uncertain
- **WHEN** 候选判断预算耗尽，仍无法可靠区分插话与干扰
- **THEN** 结束临时降音并采用明确的保守降级策略，保留输入证据；不得让音量永久降低或谎称已识别出主说话人

### Requirement: Acoustic interference capabilities are explicit
系统 MUST 区分人声活动、普通降噪、回声消除和主说话人分离。P0 SHALL 验证浏览器实际采集设置与外放回声，不能仅凭文本相同就认定为AI回声，也不能声称单通道PCM具有方向定位能力。进一步说话人聚焦 SHALL 独立启用并声明实际数据路径，不自动建立永久声纹。

#### Scenario: AI speaker output leaks into the microphone
- **WHEN** 播放中的AI语音被麦克风再次收到
- **THEN** 使用可用的声学/播放时序证据抑制自触发，证据不足时标记限制，不把整句自动转成新请求

#### Scenario: Two people overlap near the microphone
- **WHEN** 背景他人与用户同时发声，而当前部署没有声源分离能力
- **THEN** 不显示已锁定用户，不保证猜中说话人；保持可恢复输入/控制并如实记录未支持场景
