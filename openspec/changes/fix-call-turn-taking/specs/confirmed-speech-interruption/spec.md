## ADDED Requirements

### Requirement: Server confirmed speech owns interruption
系统 SHALL 使用服务端人声检测及新的识别证据确认插话，浏览器RMS仅用于音量展示，MUST 不单独触发输出epoch推进或清空播放。初始策略为持续有效人声≥300ms且有新可辨识词，或在识别滞后时持续有效人声≥800ms；明确短停止词可在有效人声/词证据后提前确认。参数 SHALL 可配置且不与三秒轮次结束参数混用。

#### Scenario: Brief non speech noise
- **WHEN** AI播放时出现短音量峰值、键盘声或未形成持续有效语音的背景噪声
- **THEN** 继续收音但不取消AI、不推进epoch、不新增问题

#### Scenario: User begins a real interruption
- **WHEN** AI正在说话，用户持续提出新请求并满足有效插话条件
- **THEN** 即刻触发现有取消/停播链路；不等待用户说完、三秒静音、校正或主Agent

#### Scenario: Recognition has not produced words yet
- **WHEN** ASR未产出新词但确认有效人声持续达到800ms
- **THEN** 系统让出话语权，继续采集用户原话，不因等待文字而持续覆盖用户声音

### Requirement: Confirmed interruption fences all old output
有效插话后系统 MUST 推进旧输出epoch，取消或作废旧LLM/TTS、清空客户端待播队列，并持续接收用户语音。未确认的候选 MUST 不改变历史delivery状态；已确认取消的尾句 MUST 不自行续播。音频播放状态与用户轮次revision SHALL 独立管理。

#### Scenario: Old synthesis completes during user speech
- **WHEN** 旧TTS在插话取消后返回
- **THEN** 旧epoch拒绝播出与状态回写，新用户输入继续缓冲到三秒停顿

#### Scenario: Noise candidate disappears
- **WHEN** 一个插话候选在确认前消失
- **THEN** 当前AI输出保持原epoch和播放状态，不产生“已中断”假记录或重新播放已播内容

### Requirement: Explicit human controls bypass speech waiting
Hinter停止/纠正、人工撤回和挂断 SHALL 继续走立即控制链路；MUST 不等待VAD、三秒、ASR或校正模型，不被上述推理持锁阻塞。Hint在校正期间更新时，主Agent及最终输出检查 SHALL 使用最新约束。

#### Scenario: Supervisor stops during refinement
- **WHEN** 文本校正请求尚未返回，Hinter点击停止或用户挂断
- **THEN** 立即停止相关输出、作废不再适用的任务；迟到校正不能复活通话或回答

#### Scenario: Supervisor adds a new ban before commit
- **WHEN** 用户turn正在整理，Hinter提交新的封禁原则
- **THEN** 校正不获取私有Hint，后续主Agent与最终检查仍按最新原则决定是否回答，自动回查和撤回继续生效

### Requirement: Honest interruption timing and echo limitations
系统 SHALL 记录有效人声开始、插话确认、客户端实际停播三个时点，分别展示检测等待与取消耗时；MUST 不用“确认后停播”冒充“用户开口到停播”。AEC与噪声抑制 SHALL 保持启用，未验证的VAD能力不得宣称能识别任意回声声源。

#### Scenario: Report interruption responsiveness
- **WHEN** 用一段可复现插话检查报告停播速度
- **THEN** 报告确认条件、确认耗时与播放停止耗时，注明设备/耳机环境，不宣称未测量的跨环境P95
