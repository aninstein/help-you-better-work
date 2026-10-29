## ADDED Requirements

### Requirement: Acoustic and semantic end of turn decisions
系统 SHALL 将有效人声、原生音频判停预测、字幕稳定性及关键槽位完整性共同用于发言权交接。自适应模式 MUST 不再将固定三秒作为每轮必要或充分条件。音频帧、ASR内部endpoint、业务问题与完整用户表达 MUST 独立处理。

#### Scenario: A clear short question is complete
- **WHEN** 用户说完一个清楚完整的英文短问，音频预测完整且字幕没有未完槽位
- **THEN** 系统在自适应窗口内交接，不强制补等三秒；只形成一个正式用户表达并启动一次后续处理

#### Scenario: A sentence pauses before its object
- **WHEN** 用户说 “I want to know whether…” 停顿两秒后继续
- **THEN** 保持倾听并合并续说，不在半句播出实质答案

#### Scenario: A question mark comes from ASR formatting
- **WHEN** 字幕被识别器加上问号，但声学和上下文显示用户尚有后文
- **THEN** 不以该标点单独结束表达

#### Scenario: Endpoint confidence is low but the intent is complete
- **WHEN** 用户说完订房等完整请求，判停预测仍偏向未结束且等待达到上限
- **THEN** 交给正常对话理解；判停不确定不单独将内容标为需要澄清，也不要求完整请求必须采用疑问句形式

### Requirement: Incomplete critical content remains incomplete
系统 MUST 对未完号码、金额、ID、否定与明确自我纠正保留等待或局部澄清；超出等待窗口不得把缺失内容猜成完整请求。用户明确要求思考空间时 SHALL 保持倾听，不因普通最长等待阈值强制回答。

#### Scenario: A caller pauses while spelling an identifier
- **WHEN** 用户说 “My account number is…” 后停顿三到五秒
- **THEN** 不自动查询或猜ID；续说能合并，仍缺失则只澄清缺少的部分

#### Scenario: Caller asks for time
- **WHEN** 用户说 “Wait, let me think” 并保持连接
- **THEN** 保持倾听且不显示无限思考，不以六秒等待到点触发实质回答

#### Scenario: Clarification is necessary after an incomplete utterance
- **WHEN** 有效等待结束后仍无法确定关键槽位
- **THEN** 可以封存一次明确标为needs_clarification的输入并挂起业务动作，最终经Hint/Wiki检查后只问缺少的内容，不把原输入标成完整可靠问题

### Requirement: Bounded local endpoint inference
系统 SHALL 优先以独立Smart Turn v3.2 CPU ONNX adapter接入现有PCM链路，使用与权重一致的16k音频窗口和预处理；SHALL 固定模型revision与哈希。推理 MUST 有界且不占媒体控制临界区，不逐帧调用主Agent，不把模型窗口当用户表达的截断限制。

#### Scenario: A long utterance exceeds the model window
- **WHEN** 用户连续表达超过八秒且包含多个问题
- **THEN** 判停可取末尾音频窗口，完整ASR片段与用户表达继续保存，不因八秒窗口丢掉前面的提问

#### Scenario: A late endpoint prediction arrives after resumed speech
- **WHEN** 用户续说使turn revision改变，旧预测随后返回complete
- **THEN** 旧预测不能提交消息、启动回答或覆盖新字幕

### Requirement: Observable fallback preserves existing call behavior
检测器不可用、超时或不支持当前输入时，系统 SHALL 回退到已有三秒有效静音策略并标明降级；已知关键槽位不完整仍需澄清。MUST 保留连续收音、两种主活动状态、多问先答/人工后补、最新Wiki检查和旧模式兼容。

#### Scenario: Endpoint model is not ready
- **WHEN** 新通话无法加载判停模型
- **THEN** 通话可在明确降级的固定静音模式继续，不无限等待模型，也不宣称自适应判停正在生效

#### Scenario: Multiple questions use separate answer routes
- **WHEN** 动态交接后的长输入中三问可自动回答，两问需要Hinter
- **THEN** 三问先回答，两问持续保留并在人工处理后关联原题补答，不要求用户逐题发言

### Requirement: Latency accounts for the actual handoff
系统 MUST 从实际语义交接冻结输入时启动原有十秒软件处理预算，不再先补等三秒；SHALL 另外报告真实语音末尾到首音播放的总时间。不能删除最终Wiki检查、提前朗读候选或用占位语音冒充实质回答来达标。

#### Scenario: An ordinary warm question receives a response
- **WHEN** 用户完成普通短问且无人工或网络查询等待
- **THEN** 分别记录判停、识别、按需校正、推理、Wiki与合成/播放耗时，以约一秒内交接及约4.5秒内首音发送为待验证优化目标，未达到则报告真实结果

#### Scenario: An external model exceeds the processing budget
- **WHEN** 交接后处理无法在共享十秒预算内完成
- **THEN** 保留问题并回到可继续倾听的状态，旧结果不得迟到发布；端到端总耗时不从报告中隐藏
