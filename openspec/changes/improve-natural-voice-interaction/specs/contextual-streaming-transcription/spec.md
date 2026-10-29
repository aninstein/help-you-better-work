## ADDED Requirements

### Requirement: Live text and final acoustic correction serve different purposes
系统 SHALL 持续在同一字幕槽呈现在线转写，区分稳定前缀与可修订尾部；完整音频二遍识别 SHALL 在声学证据上改善最终结果。ASR分片不得自行创建聊天消息，最终消息须关联输入音频范围和所用revision。

#### Scenario: Online text is revised by the second pass
- **WHEN** 在线字幕出现专名错误，二遍识别获得更充分音频后修正
- **THEN** 临时字幕可局部回改，最终用户消息使用最新有效结果，不创建两个提问或重复回答

#### Scenario: Browser shows an acronym during live recognition
- **WHEN** 在线模型给出全大写英文或逐字母的术语形式
- **THEN** 字幕可做保守可读性处理，最终识别保留API/DAU等有依据的缩写；不能把展示格式化当成识别准确性已经提高

### Requirement: Recognition context is a bounded vocabulary rather than private instructions
系统 SHALL 为ASR和专用校正提供独立、有限、按版本冻结的词汇上下文，仅包含公开或明确授权用于识别的术语/发音。MUST 不将整个Hint/Wiki、业务答案、查询结果或封禁原则作为转写指令。热词不得替代缺失音频事实。

#### Scenario: A product name is supplied for recognition
- **WHEN** Hinter明确提供可供ASR使用的产品术语，而音频有相应发音证据
- **THEN** 后续输入可用它偏置识别，并记录词表版本，不能补出用户没说的产品名

#### Scenario: A private hint supplies a business number
- **WHEN** Hinter提示“昨天DAU是1234，不要透露内部来源”
- **THEN** 该提示仍用于受限业务回答，不被放进ASR提示或用来把用户说的其他数值改成1234

### Requirement: Direct transcript refinement is conditional and evidence preserving
系统 SHALL 保留独立固定直连的TranscriptRefiner，不经过主Agent、工具或Wiki；MUST 根据音频/转写质量与关键槽位检查选择直通、校正、降级或澄清，而非每轮强制串行LLM调用。直通MUST有可见内部状态，不能谎称AI校正完成。所有修复共享现有处理预算。

#### Scenario: The final English transcription is already clear
- **WHEN** 最终转写自然可读、槽位完整且未见音频质量或关键内容冲突
- **THEN** 可以verified_passthrough提交，不再为走流程等待一次额外模型调用；实际不确定度不能仅凭流畅度掩盖

#### Scenario: A correctable wording issue remains
- **WHEN** 有依据的大小写、重复或术语问题需要专用agent调整
- **THEN** 直接调用该agent并限制剩余时长，失败只降级或局部澄清，不进入主模型池长重试

#### Scenario: Two passes disagree on an important number
- **WHEN** 识别结果对fifteen/fifty、ID或否定存在未能解决的分歧
- **THEN** 不让LLM凭常识选值或执行查询，只澄清有歧义的部分，同时保留其余独立问题

### Requirement: Audio fidelity and loss are observable
采集链路 SHALL 记录实际采样配置并使用经过验证的16k重采样；媒体协议 SHALL 能识别丢失、重复和不连续的样本范围。缺包、网络不送数据或被主动丢弃的帧 MUST 不算作有效静音，也不得默默拼成高置信完整话语。

#### Scenario: Audio device runs at 48 kHz
- **WHEN** 实际设备/AudioContext采样率与目标16k不一致
- **THEN** 使用验证过的转换并记录真实配置，不靠声明采样率假装音频格式正确

#### Scenario: Network backpressure drops audio frames
- **WHEN** 输入seq或sample范围出现无法恢复的缺口
- **THEN** 标明受影响输入并请求必要澄清，不因缺口触发用户已说完，不让残缺金额/ID进入查询

### Requirement: Better recognition preserves supervision and withdrawal boundaries
新增术语、识别证据、临时字幕与诊断字段 MUST 继承角色隔离和撤回隐藏。系统 SHALL 保持简洁的倾听/思考反馈、现有多问调度、Hinter实时提示和每条最终业务答复的最新Wiki检查。

#### Scenario: A new hint arrives while transcription is being corrected
- **WHEN** 校正尚未提交，Hinter更新业务回答限制
- **THEN** 校正不读取该私有指令改写原话，提交后的回答使用最新约束，必要时挂起而非输出旧候选

#### Scenario: An answer or associated hidden content is withdrawn
- **WHEN** 人工撤回或最近五题封禁回查生效
- **THEN** 新增识别/词汇/诊断投影也不能恢复已隐藏内容，旧播放及恢复事件均不能使其重新出现
