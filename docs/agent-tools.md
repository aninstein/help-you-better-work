# Agent 工具、MCP 与 Skills

## 当前消息／语音链路与诊断（2026-10-23）

当前页面通过 `ResponseService` / `CallReasoner` 自动回答。公开查询的 `action=lookup` 不再被 `data_lookup` 主题分类覆盖为人工待补；人工处理旧问题时也会恢复明确的公开搜索意图。网页搜索实际获取正文后，以完成的工具消息传给回答模型；失败记录为工具失败，不把未执行或无关内容当作证据。

所有 Talker 正式输入先经过一次 LLM 语义清理，保留原文与有效文本；高质量 ASR 也不能跳过。搜索随后将中英文公开查询整理成简短英文关键词，去掉 “OK / um / 帮我调用 web search”等非搜索内容。范围判断可使用私有 Wiki／Hint，但搜索表达只使用已确定为公开的问题，不把私有上下文作为搜索词。检索最多尝试两组查询，读取正文后再判断是否确实支持问题；天气必须有对应地点和时效依据，百科 OK 或气候概述不能算成功。默认 Bing 同时检索网页与新闻，按主题覆盖排序后截取结果，避免 RSS 返回首页、登录页时完全丢失指标文章。读取最多五个候选正文，不因前两个页面先返回而取消其他候选；长文优先保留 main/article 正文及匹配问题的段落。核验模型选择实际支持答案的来源。预读搜索总预算最多 30 秒；失败时 Hinter 的 `work` 中保留查询尝试、正文读取数量、错误类型和证据判断原因，区分提供方不可用、正文读取失败、证据不足及超时。

当前链路也可调用注册的 MCP、skill 与本地工具；一般对话保持流式输出，工具回合的内部文字不会公开。对工具查询使用独立的准备预算，避免沿用普通语音回合的短超时直接打断查询。飞书 CLI 响应先解析成功状态和正文，再裁剪长文档；Hinter 的链接参考文档也实际执行读取，认证失败不会显示为读取成功。

Hinter 可调用 `GET /api/v1/agent/tools` 查看当前工具、搜索提供方、skills 和已注册的 MCP 服务；加 `?probe=true` 会连接已配置的 MCP 进行发现。接口不返回 MCP 密钥或 HTTP 认证头。每题的 `work` 字段记录工具名、执行状态和来源，仅在 Hinter 的会话快照中返回。

注册能力与安装服务是两件事：当前默认 `mcp_servers` 为空，除非操作者明确添加服务器，否则不会凭空出现外部 MCP 工具。本次修复没有额外安装服务器或修改飞书登录与权限。

## 自动回答与人工介入

默认由 AI 自动回答。普通知识、定义、闲聊和主观讨论直接回答；天气、公开公司经营指标等可能公开的数据先使用工具核实，不因指标名称、具体数字、未知答案或 `data_lookup` 分类而转人工。公开资料必须区分统计周期、活跃用户口径、地域范围，以及官方数值和估算。缺少城市、识别冲突等歧义向 Talker 澄清。工具失败说明限制，问题状态为 `unavailable`（暂未完成），不标记为已回答，不发起人工审批。

只有两类情况主动让 Hinter 介入：命中用户指定的人工确认规则（`human_confirmation_required`），或明确需要内部/私有数据且缺少匹配的已核实事实（`human_evidence_required`）。单纯的提示冲突不创造新的审批要求，应给出受限答复或澄清。Hinter 暂停自动回答属于独立开关，不是逐题审批。

命中适用的禁止回答规则时，正常发送并播报 `I can't answer that question.`，结果标记为已完成的拒答，不搜索、不生成澄清或进度播报，也不转人工审批。规则适用范围由模型结合当前输入、上下文和 Wiki 判断，不按产品名称写死。禁止规则优先于人工确认、已知事实和公开搜索；语音槽位有歧义时仍先检查禁答范围。该固定拒答不含业务事实或私有规则，只允许这句原文通过发布校验，其他内容仍须校验。同一问题重试不重复发送已完成的拒答。历史空失败卡片在拒答成功后由正常拒答取代；原始尝试仍留在存储中，已发送的真实正文不因去重而丢失。

“仅后续生效”的规则在首次路由时按当前消息 ID 和创建时间判定，在发布时按问题的源消息判定，两阶段使用一致的生效边界。历史 `hinter-guidance.md` 和会话逐字记录保留供用户查看及记忆整理；实时模型使用知识文档、当前作用域内的有效提示和单独提供的近期对话，不能把审计记录中的旧禁令或旧拒答当成当前规则。

路由模型结合当前问题、近期会话及 Wiki 输出 `data_scope`、`scope_reason`、`evidence_status`、`evidence_quotes` 和 `evidence_gap`。不按产品名称写死内部范围：同一产品的内部试点结果可需要 Hinter，其公开报告则先搜索；无法判明实体或范围时澄清。Wiki 中的项目归属、保密性和背景不是所问指标的数值。判定已有充分依据时必须引用实际提供的匹配事实，服务端校验引用存在，不能凭模型声明“用了 Wiki”跳过查询。指定用户记录、凭据和明确内部查询仍有独立的公开搜索保护。

Hinter 纠正并重答时，完整补充内容会绑定到当前问题。服务端取消该题尚未结束的旧搜索，再重新判断证据是否匹配；已有匹配事实直接回答，不让旧的 `web_query` 再次抢占。补充 DAU／MAU 无法填补营收问题，答复应说明指标不匹配和仍缺少的内容，不能编造营收或重复旧搜索错误。公开问题的搜索失败状态也允许新事实触发重答，缺少部分事实时仍保持暂未完成或待内部补充。“Try again”复用原问题和原查询，不创建以该短语为搜索词的新问题。

公开查询的搜索词若被路由模型翻译、改写或漏填，执行层会从当前用户原话恢复安全查询，避免因“不是原文连续片段”而误转人工。路由只允许提取当前输入，历史记录用于理解指代；历史问题不会重新加入本轮，无法对应当前输入时至多去掉历史重试一次。显式人工确认规则仍优先保留。

公开检索完成后直接根据已配对的工具结果生成答复，不因模型的 `use_tools` 标记而重复启动同一检索。发布校验使用已配置的主回答模型，轻量模型继续负责路由，避免把一个产品的禁令扩大到指标定义或其他产品。模型等待、逐段校验和 TTS 准备分别限时：发布校验和有界音频队列的正常等待不消耗模型生成预算，已生成的长答案不会因语音播放超过生成限时而被误报失败。每段 TTS 有独立准备预算；首响延迟仍按真实时间记录。

当前消息和语音链路默认自动发布英文回答，只有上述两类情况等待 Hinter。历史草稿链路仍支持拟答后确认发送；工具执行发生在拟答期间，确认发布只控制答案发送，不控制此前的本地文件操作。工具输出和中间规划不会作为最终回答流展示。

## 内置能力

|工具|用途|配置|
|---|---|---|
|`web_search`|联网搜索，返回标题、URL、摘要|默认 Bing RSS，无需 Key；可切 Brave 或 SearXNG|
|`web_fetch`|读取公开 HTML/文本来源，供核实和引用|拒绝私网地址，逐跳校验重定向；不解析 PDF|
|`lark_doc_read`|通过已安装的 lark-doc skill 调用 `lark-cli docs +fetch --as user`|复用运行服务用户的飞书登录状态|
|`bash`|读取、查找、创建和编辑本地文件，执行已安装 skill 的命令|默认工作目录为项目根目录，使用后端进程用户权限|
|`read_skill`|按需读取 SKILL.md 和该 skill 内的参考文件|默认发现 `.agents/skills`、`.codex/skills`|
|`calculate_expression`|现有数值计算器|继续可用|

`bash` 是实际本地 shell，没有文件系统沙箱。只有用户授权的本地动作应被执行；耗时和输出有上限。当前服务仍采用原有本地 Demo 身份机制，适合可信本机使用，不应作为公开网络上的通用 shell 服务。可在配置中设置 `bash_enabled: false` 禁用。

## 配置

无配置文件时内置工具直接可用。需要调整时，将根目录 `agent-tools.example.json` 复制为 `agent-tools.json`。配置逐次拟答重新读取；不需要重启。`DUET_TOOLS_CONFIG` 可指定其他路径，配置中的相对工作目录和 skill 路径均相对项目根目录解析。

实际 `agent-tools.json` 已忽略提交，CLI 写入权限为 600。MCP token 可以保存在环境变量中，在配置值里用 `${VARIABLE}` 引用。不要把凭据写进示例文件。

搜索提供方：

- `bing`：默认合并公开网页与新闻 RSS，无需额外账号；该接口不是有可用性保证的商业搜索 API，结果质量和地区覆盖取决于服务端。
- `brave`：设置 `web_search_provider: "brave"`，并在 `.env` 配置 `BRAVE_SEARCH_API_KEY`。
- `searxng`：设置 `web_search_provider: "searxng"` 和 `searxng_url`，实例需允许 JSON 搜索响应。

默认每次拟答最多 8 轮、24 次工具执行，每次超时 30 秒、输出上限 18000 字符。可修改 `max_rounds`、`max_calls`、`timeout_seconds`、`max_output_chars`。超限时模型只能根据已有证据回答。截断输出会被标记，飞书文档可用 keyword、outline、section、range 缩小读取范围。

## 安装和管理

以下命令从项目根目录执行。安装属于本地操作者行为，没有提供可通过聊天自动安装扩展的管理工具。

```bash
# 查看工具、skill 元数据和缺失依赖，不连接外部 MCP
(cd backend && .venv/bin/python -m app.agent.extensions status)

# 连接已配置的 MCP，查看实际发现的工具和连接错误
(cd backend && .venv/bin/python -m app.agent.extensions status --probe)

# 安装一个指定 skill：固定项目根目录、Codex、非交互，不添加 -g
(cd backend && .venv/bin/python -m app.agent.extensions skill-add github.com/larksuite/cli --skill lark-doc)

# 注册 stdio MCP；npx 在首次启动时按参数安装/运行包
# 替换包名及绝对目录后运行；注册本身不启动服务
(cd backend && .venv/bin/python -m app.agent.extensions mcp-add filesystem \
  --command npx --arg=-y --arg=@modelcontextprotocol/server-filesystem \
  --arg=/absolute/path/to/allowed-directory)

# 注册 Streamable HTTP MCP，凭据由后端环境提供
(cd backend && .venv/bin/python -m app.agent.extensions mcp-add remote \
  --url https://your-server.example/mcp \
  --header 'Authorization=Bearer ${MCP_ACCESS_TOKEN}')

# 移除注册（不删除 npm 缓存或远端服务）
(cd backend && .venv/bin/python -m app.agent.extensions mcp-remove remote)
```

`skill-add` 内部使用用户指定的 AgentBuddy 安装方式：

```bash
npm_config_registry="https://registry.npmjs.org" npx agentbuddy@latest skill add github.com/larksuite/cli --skill lark-doc -a codex -y
```

CLI 的 `--registry` 可覆盖 registry，默认按本项目要求使用 bnpm；每次只选择一个 skill 和 Codex，工作目录固定为项目根目录，不安装到用户 home。skill 安装元数据保留在 `agentbuddy.yaml` 和 `agentbuddy-lock.yaml`。需要添加其他 skill 时，由操作者明确选择其 source 和名称。

MCP 支持 stdio 和 Streamable HTTP 的 JSON/SSE 响应、初始化握手、会话 ID、分页工具发现和工具调用。每次拟答创建并关闭会话；一个服务连接失败不阻断其他工具。支持 `enabled: false` 和 `allowed_tools` 白名单（CLI `--allow-tool` 可重复传入），工具名称加服务命名空间以避免冲突。当前实现只提供 tools 能力，不提供 resources、prompts、sampling、elicitation、旧版 HTTP+SSE 或 OAuth 登录流程；HTTP 凭据由操作者配置。

## 飞书文档安装与依赖

本次只安装了 `github.com/larksuite/cli` 的 `lark-doc`，位于项目 `.agents/skills/lark-doc`，安装目标为 Codex。本机已有 `lark-cli`，无需重新安装。skill 元数据还声明了 `lark-shared` 依赖；按用户限定，本次没有安装该 skill，也没有改动全局登录、scope 或其他 agents 的设置。

读取时先读取安装的 SKILL.md 和 `references/lark-doc-fetch.md`，再通过独立 argv 参数调用 CLI，不把链接拼接为 shell 命令。保持默认 JSON 响应中的正文、评论 sidecar 和权限提示。未提供真实文档链接，因此没有为验证而访问用户的私有文档或启动登录。

当批准问题或其原始消息包含 `/docx/<token>`、`/wiki/<token>` URL 时，Agent 会自动读取，覆盖飞书、Lark、Doubao 及自定义域名。最多预读前 5 个链接，其他链接可继续通过工具读取。`/page/`、`/sheets/`、`/base/` 不按云文档读取。公开网页读取工具遇到文档链接也会转到飞书读取，避免把私有链接发给搜索引擎。

实际读取如返回未认证、身份或 scope 错误，应按 lark-shared 的认证指引由操作者处理后重试；缺少依赖会在 `status` 中显示，不会自动安装或扩大授权。没有读取成功时必须说明限制，不能生成虚构摘要。Docker 镜像包含 skill 文件、Node/npm/npx 和 bash；飞书 CLI、Git、第三方 MCP 服务及账号授权按需另行配置。容器 HOME 为 `/app/data/home`，配置随数据卷保留；首次生成的 `/app/data/agent-tools.json` 将本地工具工作目录设为 `/app/data/workspace`，后续重启不会覆盖已有设置。可用 `docker compose exec web python -m app.agent.extensions status` 检查工具；配置 MCP 时使用容器可访问的服务地址与路径。

## Web Search 提示词依据

参考了 OpenAI Codex 开源仓库提交 `b4ad33d1a3c10c9652dd57f7afd7a949f8f26895`：

- [Web Search 工具说明与决策边界](https://github.com/openai/codex/blob/b4ad33d1a3c10c9652dd57f7afd7a949f8f26895/codex-rs/ext/web-search/web_run_description.md)
- [工具定义、描述注入与调用实现](https://github.com/openai/codex/blob/b4ad33d1a3c10c9652dd57f7afd7a949f8f26895/codex-rs/ext/web-search/src/tool.rs)
- [官方 Codex Prompting Guide：Other Custom Tools](https://developers.openai.com/cookbook/examples/gpt-5/codex_prompting_guide)

Codex 在工具描述中明确规定搜索场景，由模型结合任务决定调用；实现侧负责提供工具定义并执行查询。本项目采用同样的职责划分，适配后的核心提示词在 `backend/app/agent/prompts/tools.md`，同时把简短用途放入工具 description：

1. 用户明确要求查证、事实具有时效性、知识不确定、需要准确来源或涉及高风险事实时搜索。
2. 用户禁止联网时遵从；纯改写、翻译、普通计算、已有材料足够的问题通常直接回答。
3. 搜索摘要仅用于发现来源，再读取原文核实；技术问题优先官方资料，区分新闻发布日期和事件日期。
4. 最终回答使用真实来源链接，失败不冒充成功；私有飞书内容不能被发送给公共搜索引擎。

本项目的搜索后端是 Bing/Brave/SearXNG，不复用 Codex 的内部认证或搜索服务。工具循环会为每个调用配对 ToolMessage，隔离中间规划内容。主模型失败切换备用模型时，相同工具名和参数的执行结果在本次拟答内复用，减少相同文件修改被重复执行的风险；不同命令不具备跨请求事务或自动回滚保证。

Bing 网页与新闻请求使用 `format=RSS`，不强制 `mkt`，避免地区重定向循环及新闻接口返回 HTML。多指标请求先检索其中一个指标，允许有依据的部分回答，并明确其他指标的缺口。公开证据校验使用主模型提取原文引用，服务端核实引用确实存在于对应正文；广告收入、区域收入或公开估算只能按原口径说明，不能冒充全球总营收或官方实际值。当天的天气查询额外发现 Open-Meteo 的公开数据源，按解析出的城市定位，再读取当前模型天气和当天预报；服务器检查时区、更新时间和温度字段，过期数据不能由城市介绍或历史天气新闻替代。接口参数见 [Geocoding API](https://open-meteo.com/en/docs/geocoding-api) 与 [Forecast API](https://open-meteo.com/en/docs)。

## 验证

```bash
(cd backend && .venv/bin/pytest -q)
backend/.venv/bin/ruff check backend/app backend/tests
```

测试覆盖真实 bash 文件读写及超时、skill 元数据与路径边界、飞书 argv 和认证错误保留、stdio MCP 子进程及分页、HTTP JSON/SSE 与会话清理、多轮工具协议、原始消息链接预读、模型切换时相同命令去重，以及调用预算。联网与真实模型验证独立执行，不作为离线测试的依赖。

本次离线 13 项测试通过，Ruff 与编译检查通过。真实联网验证了 Bing 搜索及网页正文读取；现有模型池实际执行了 `web_search` → `web_fetch` → 带来源链接的答案，并验证了主模型失败后的备用模型接管。飞书使用模拟 CLI 验证命令参数和错误传递，真实私有文档权限尚待提供文档链接后验证。
