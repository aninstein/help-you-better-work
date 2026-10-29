## ADDED Requirements

### Requirement: A consistent personal assistant with attributed memory
三种入口 SHALL 共用专业个人助手人格：自然、温和、务实，能直接表达有依据的观点和推荐。助手人格、用户偏好与授权事实 MUST 保留各自归属，不把个人交流一律变成查记忆或追问。正式对话模型与轻量拆题、转写模型 SHALL 独立配置。

#### Scenario: The assistant and user have different favourite films
- **WHEN** Wiki记录用户喜欢另一部电影，用户询问“你最喜欢什么电影”
- **THEN** 助手直接给自己的推荐和理由；只有问“我最喜欢什么电影”时才使用相关用户记忆，助手推荐也不得回写成用户的偏好

#### Scenario: A meaningful personal update has no question mark
- **WHEN** 用户分享完成项目等近况
- **THEN** 助手按正常对话回应，不因没有问号而沉默，不复述为用户自己的第一人称，也不强行引入无关记忆

### Requirement: Automatic replies across all input modes
系统 SHALL 对新文本、上传语音转写和电话语义轮次默认自动回复，不要求逐题审核、私有草稿确认或启用旧 ai_mode。问题记录 MUST 不隐含人工等待。

#### Scenario: Ordinary input without an operator
- **WHEN** Talker 在任一入口提交普通问题且 Hinter 未操作
- **THEN** 系统按适用 Wiki 自动构建并公开回复，记录问题与真实 human_reviewed=false

### Requirement: Separate clarification refusal and capability failures from human review
系统 SHALL 自行澄清缺失槽位、安全拒绝伤害性协助、说明工具或数据能力限制；新话题、主观问题、未知分类、轻微脏话与处理失败 MUST 不单独触发人工等待。

#### Scenario: Philosophy and incomplete request
- **WHEN** Talker 分别询问人生意义以及一个缺少对象的问题
- **THEN** 前者获得正常回答，后者获得面向 Talker 的简短澄清，均不创建人工待办

#### Scenario: Unsafe request or failed lookup
- **WHEN** 请求可操作的暴力协助或一次公开检索失败
- **THEN** 系统分别自动安全拒绝或说明无法核验，不转成等待人工放行

#### Scenario: Speech clarification uses the actual conversation
- **WHEN** 某段语音有具体歧义，或用户在上次澄清后补充、重说
- **THEN** 根据有角色的对话历史只追问实际不确定的部分；不得统一循环播放姓名/数字/ID模板，不把用户的话当作AI自己的话，也不因二遍识别暂不可用而阻断可理解的意图

### Requirement: Narrow explicit human exceptions
系统 MUST 仅为有效 require_human 规则、确需人工提供且有接收渠道的非公开依据、无法安全消解的适用约束冲突创建人工待补；每项 SHALL 包含可解决的 reason、作用问题、缺失信息和命中规则。禁止项 MUST 直接遵守，不反复索取人工许可。

#### Scenario: Explicit price confirmation
- **WHEN** Hinter 已设正式报价须确认且 Talker 同时询问产品定义和正式价格
- **THEN** 定义自动回答，仅价格等待对应人工确认

#### Scenario: Previously forbidden identity disclosure
- **WHEN** Wiki 中有有效身份披露禁令且 Talker 再次询问相同身份
- **THEN** 系统给允许的边界回复，不披露身份且不创建新的许可请求

### Requirement: Truthful data and tool boundaries
系统 MUST 不编造指标、身份或查询成功，不把非公开标识与约束发送公开搜索。无数据源 SHALL 明确限制；有指定人工信息渠道时可同时创建原题待补。三入口自动模型 MUST 只获得明确允许的只读工具，不能继承旧私有草稿流程中的任意 bash、未声明 MCP 或外部写入能力。

#### Scenario: Missing actual DAU
- **WHEN** Talker 询问昨天公司 DAU 且只有 Hinter 能提供已核实数字
- **THEN** 系统先说明不能立即核验，保留待人工依据的原题，其他题继续；定义 DAU 不触发相同等待

#### Scenario: Legacy tool configuration includes bash
- **WHEN** 旧扩展配置开启 bash 和其他 MCP，而新输入启用自动回答
- **THEN** 自动模型仅注册明确允许的只读工具，不因答案免审核获得原本受人工流程约束的写入能力

### Requirement: Independent resolution and delayed supplements
系统 SHALL 保留长表达全部问题，独立调度可回答部分，按证据依赖处理待补部分；补答 MUST 绑定原题和原输入，而非最新消息。

#### Scenario: Five questions with one exception
- **WHEN** 一段发言包含四个普通问题和一个需人工依据的问题
- **THEN** 四题自动回答，仅例外保留待补，人工提供依据后关联原题补答

### Requirement: Nonblocking observation and manual intervention
Hinter SHALL 看见持续更新的公开 AI 气泡、问题处理结果及少量可行动待补，并可随时提交 Hint、暂停或撤回；普通问题 MUST 不要求“确认发送”。

#### Scenario: Watching ordinary conversation
- **WHEN** AI 连续回答普通问题
- **THEN** Hinter 页面显示答案且不逐题弹审核，仍可撤回任意已发气泡

### Requirement: Safe historical migration and output ownership
系统 MUST 不自动公开历史私有草稿或批量重答旧待审题；新输入 SHALL 使用新默认行为且不被遗留草稿阻塞。模式切换 MUST 取消旧输出所有权，阻止迟到结果插入新模式。

#### Scenario: Dial with an old draft
- **WHEN** 存在旧私有草稿而 Talker 开始电话
- **THEN** 草稿保持私有，电话可进入倾听，旧任务不插播；明确人工暂停仍有效
