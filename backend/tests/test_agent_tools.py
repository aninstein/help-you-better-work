"""Exercise actual tool execution, MCP transports and model tool-message boundaries."""

import asyncio
import json
import sys

import httpx
import pytest
from langchain_core.messages import AIMessageChunk, ToolMessage

from app.agent import voice_agent
from app.agent.extensions import main
from app.agent.mcp_client import McpClient
from app.agent.model_router import ModelRouter
from app.agent.skills import SkillCatalog
from app.agent.tool_config import McpServer, ToolConfig, load_tool_config
from app.agent.tools import ToolRuntime, document_links
from app.agent.voice_agent import VoiceAgent
from app.domain.models import ModelSettings


def run(coroutine):
    return asyncio.run(coroutine)


@pytest.fixture
def config(tmp_path):
    root = tmp_path / "skills" / "lark-doc"
    (root / "references").mkdir(parents=True)
    (root / "SKILL.md").write_text(
        '---\nname: lark-doc\ndescription: "Read docs"\nmetadata:\n'
        "  requires:\n    bins: [lark-cli]\n    skills: [lark-shared]\n---\nRead docs."
    )
    (root / "references/lark-doc-fetch.md").write_text("Use docs +fetch --as user.")
    return ToolConfig(workspace=str(tmp_path), skill_dirs=[str(tmp_path / "skills")])


def test_skills_and_document_routing(config):
    catalog = SkillCatalog(config)
    assert catalog.inventory()[0]["missing_skills"] == ["lark-shared"]
    assert "docs +fetch" in catalog.read("lark-doc", "references/lark-doc-fetch.md")
    with pytest.raises(ValueError):
        catalog.read("lark-doc", "../../outside")
    assert document_links(
        "请总结 [文档](https://team.feishu.cn/docx/ABC?x=1#share-a)，以及 "
        "https://doubao.com/wiki/DEF。https://team.larksuite.com/page/xyz "
        "https://team.feishu.cn/base/ghi https://team.feishu.cn/docx/ABC?x=1#share-a"
    ) == ["https://team.feishu.cn/docx/ABC?x=1#share-a", "https://doubao.com/wiki/DEF"]


def test_bash_reads_writes_truncates_and_times_out(config, tmp_path):
    async def scenario():
        async with ToolRuntime(config) as runtime:
            output = json.loads(
                await runtime.execute(
                    "bash", {"command": "printf 'hello' > 'test file.txt'; cat 'test file.txt'"}
                )
            )
            assert output["ok"] and output["stdout"] == "hello"
            assert (tmp_path / "test file.txt").read_text() == "hello"
            large = json.loads(
                await runtime.execute("bash", {"command": "yes output | head -c 50000"})
            )
            assert large["truncated"]
            timeout = json.loads(
                await runtime.execute("bash", {"command": "sleep 10", "timeout_seconds": 1})
            )
            assert not timeout["ok"] and timeout["error"] == "TimeoutError"
            bad = json.loads(await runtime.execute("nonexistent", {}))
            assert not bad["ok"]

    run(scenario())


def test_lark_uses_argv_and_preserves_auth_errors(config, tmp_path):
    executable = tmp_path / "fake-lark"
    executable.write_text(
        f"#!{sys.executable}\nimport json,sys\n"
        "assert sys.argv[1:3] == ['docs', '+fetch']\n"
        "assert sys.argv[sys.argv.index('--as') + 1] == 'user'\n"
        "print(json.dumps({'ok':False, '_notice':'AUTH_REQUIRED', 'argv':sys.argv[1:]}))\n"
        "sys.exit(1)\n"
    )
    executable.chmod(0o700)
    config.lark_cli = str(executable)

    async def scenario():
        async with ToolRuntime(config) as runtime:
            result = json.loads(
                await runtime.execute(
                    "lark_doc_read",
                    {
                        "url": "https://doubao.com/wiki/ABC",
                        "scope": "keyword",
                        "keyword": "部署|发布",
                    },
                )
            )
            assert not result["ok"]
            assert "AUTH_REQUIRED" in result["stdout"]
            assert json.loads(result["stdout"])["argv"][-2:] == ["--keyword", "部署|发布"]
            with pytest.raises(ValueError):
                await runtime.lark_doc_read("https://example.org/page/ABC")

    run(scenario())


def test_private_document_links_never_go_to_public_search(config):
    async def scenario():
        async with ToolRuntime(config) as runtime:
            result = json.loads(
                await runtime.execute(
                    "web_search", {"query": "Summarize https://team.feishu.cn/docx/ABC"}
                )
            )
            assert not result["ok"] and "lark_doc_read" in result["message"]
            blocked = json.loads(await runtime.execute("web_fetch", {"url": "http://127.0.0.1/"}))
            assert not blocked["ok"] and "private" in blocked["message"]

    run(scenario())


@pytest.fixture
def mcp_server(tmp_path):
    script = tmp_path / "mcp_server.py"
    script.write_text("""import json,sys
initialized = False
for line in sys.stdin:
    m = json.loads(line)
    method = m.get('method')
    if method == 'notifications/initialized':
        initialized = True
        continue
    if method == 'initialize':
        result = {'protocolVersion':'2025-03-26','capabilities':{'tools':{}},
                  'serverInfo':{'name':'fixture','version':'1'}}
    elif method == 'tools/list':
        assert initialized
        if m.get('params',{}).get('cursor'):
            result = {'tools':[{'name':'blocked','inputSchema':{'type':'object','properties':{}}}]}
        else:
            result = {'tools':[{'name':'echo','description':'Echo text', 'inputSchema':{
                'type':'object','properties':{'text':{'type':'string'}},'required':['text']}}],
                'nextCursor':'page2'}
    elif method == 'tools/call':
        result = {'content':[{'type':'text','text':m['params']['arguments']['text']}]}
    else:
        continue
    print(json.dumps({'jsonrpc':'2.0','id':m['id'],'result':result}),flush=True)
""")
    return McpServer(command=sys.executable, args=[str(script)], allowed_tools=["echo"])


def test_mcp_stdio_discovery_calls_and_failure_isolation(config, mcp_server):
    config.mcp_servers = {
        "broken": McpServer(command="/nonexistent/command"),
        "fixture": mcp_server,
    }

    async def scenario():
        async with ToolRuntime(config) as runtime:
            names = [name for name in runtime.tools if name.startswith("mcp_")]
            assert len(names) == 1 and "fixture_echo" in names[0]
            result = json.loads(await runtime.execute(names[0], {"text": "MCP works"}))
            assert result["content"][0]["text"] == "MCP works"
            assert runtime.unavailable == ["broken: FileNotFoundError"]
            assert "bash" in runtime.tools

    run(scenario())


@pytest.mark.parametrize("sse", [False, True])
def test_mcp_http_json_sse_and_session_cleanup(monkeypatch, tmp_path, sse):
    seen = []

    def handler(request):
        seen.append(request)
        if request.method == "DELETE":
            return httpx.Response(204)
        message = json.loads(request.content)
        if message["method"] == "initialize":
            result = {"protocolVersion": "2025-03-26", "capabilities": {"tools": {}}}
        elif "id" not in message:
            assert request.headers["Mcp-Session-Id"] == "session-123"
            return httpx.Response(202)
        else:
            assert request.headers["Mcp-Session-Id"] == "session-123"
            result = {"tools": []}
        reply = {"jsonrpc": "2.0", "id": message["id"], "result": result}
        if sse:
            return httpx.Response(
                200,
                headers={"Content-Type": "text/event-stream", "Mcp-Session-Id": "session-123"},
                text="event: message\ndata: " + json.dumps(reply) + "\n\n",
            )
        return httpx.Response(200, headers={"Mcp-Session-Id": "session-123"}, json=reply)

    original = httpx.AsyncClient
    monkeypatch.setattr(
        httpx, "AsyncClient", lambda **kw: original(transport=httpx.MockTransport(handler), **kw)
    )

    async def scenario():
        client = McpClient(McpServer(transport="http", url="https://mcp.example/mcp"), tmp_path, 2)
        async with client:
            assert await client.list_tools() == []

    run(scenario())
    assert seen[-1].method == "DELETE"


class Model:
    def __init__(self, outputs):
        self.outputs = iter(outputs)
        self.seen = []
        self.bound_names = []

    def bind_tools(self, tools):
        self.bound_names = [tool.name for tool in tools]
        return self

    async def astream(self, messages):
        self.seen.append(list(messages))
        output = next(self.outputs)
        if isinstance(output, Exception):
            raise output
        yield output


def agent_with(*clients):
    router = ModelRouter.__new__(ModelRouter)
    router.models = [ModelSettings(model=f"test-{index}") for index in range(len(clients))]
    router.clients = list(zip(router.models, clients, strict=True))
    return VoiceAgent(router)


def call(name, args, identifier="call-1", content="Internal planning, never show this."):
    return AIMessageChunk(
        content=content,
        tool_calls=[
            {
                "name": name,
                "args": args,
                "id": identifier,
            }
        ],
    )


async def collect(agent, **kwargs):
    text = ""
    async for part in agent.answer(["Please help"], "", [], "", "en-US", **kwargs):
        text = "" if part.reset else text + part.text
    return text


def test_multiround_tools_without_digits_and_no_planning_leak(config, monkeypatch):
    monkeypatch.setattr(voice_agent, "load_tool_config", lambda: config)

    async def search(self, query, max_results=5, domains=None):
        return {"results": [{"url": "https://example.org", "snippet": "Verified"}]}

    monkeypatch.setattr(ToolRuntime, "web_search", search)
    model = Model(
        [
            call("read_skill", {"name": "lark-doc"}),
            call("web_search", {"query": "current news"}, "call-2"),
            AIMessageChunk(content="Verified answer [source](https://example.org)."),
        ]
    )
    result = run(collect(agent_with(model)))
    assert result == "Verified answer [source](https://example.org)."
    assert "web_search" in model.bound_names
    assert sum(isinstance(m, ToolMessage) for m in model.seen[-1]) == 2


def test_document_links_preload_even_when_intake_omits_them(config, monkeypatch):
    monkeypatch.setattr(voice_agent, "load_tool_config", lambda: config)
    fetched = []

    async def read(self, url, **kwargs):
        fetched.append(url)
        return {"ok": True, "document": "Source evidence"}

    monkeypatch.setattr(ToolRuntime, "lark_doc_read", read)
    model = Model([AIMessageChunk(content="Document summary.")])
    run(collect(agent_with(model), source_texts=["请读 https://doubao.com/wiki/ABC"]))
    assert fetched == ["https://doubao.com/wiki/ABC"]
    assert sum(isinstance(m, ToolMessage) for m in model.seen[0]) == 3


def test_failover_does_not_repeat_identical_file_mutation(config, monkeypatch, tmp_path):
    monkeypatch.setattr(voice_agent, "load_tool_config", lambda: config)
    args = {"command": "printf x >> side-effect.txt"}
    first = Model([call("bash", args), RuntimeError("model offline")])
    second = Model([call("bash", args, "retry"), AIMessageChunk(content="Done.")])
    assert run(collect(agent_with(first, second))) == "Done."
    assert (tmp_path / "side-effect.txt").read_text() == "x"


def test_budget_pairs_every_tool_call_and_forces_final_answer(config, monkeypatch):
    config.max_calls = 1
    monkeypatch.setattr(voice_agent, "load_tool_config", lambda: config)
    model = Model(
        [
            AIMessageChunk(
                content="",
                tool_calls=[
                    {"name": "unknown", "args": {}, "id": "a"},
                    {"name": "unknown2", "args": {}, "id": "b"},
                ],
            ),
            AIMessageChunk(content="Missing evidence."),
        ]
    )
    assert run(collect(agent_with(model))) == "Missing evidence."
    results = [m for m in model.seen[-1] if isinstance(m, ToolMessage)]
    assert [m.tool_call_id for m in results] == ["a", "b"]
    assert "budget" in results[-1].content


def test_extension_cli_uses_project_install_and_no_global_flag(monkeypatch, tmp_path):
    commands = []

    class Result:
        returncode = 0

    def execute(argv, **kwargs):
        commands.append((argv, kwargs))
        return Result()

    monkeypatch.setattr("app.agent.extensions.subprocess.run", execute)
    assert main(["skill-add", "github.com/larksuite/cli", "--skill", "lark-doc"]) == 0
    argv, options = commands[0]
    assert argv[-5:] == ["--skill", "lark-doc", "-a", "codex", "-y"]
    assert "-g" not in argv and options["env"]["npm_config_registry"] == "https://registry.npmjs.org"
    monkeypatch.setenv("DUET_TOOLS_CONFIG", str(tmp_path / "config.json"))
    assert main(["mcp-add", "fixture", "--command", "npx", "--arg=-y", "--arg=example"]) == 0
    assert load_tool_config().mcp_servers["fixture"].args == ["-y", "example"]
    assert main(["mcp-remove", "fixture"]) == 0
    assert not load_tool_config().mcp_servers


def test_current_call_reasoner_can_discover_and_execute_mcp(config, mcp_server, monkeypatch):
    from langchain_core.messages import AIMessage

    from app.agent import call_reasoner
    from app.agent.call_reasoner import CallReasoner

    config.mcp_servers = {"fixture": mcp_server}
    monkeypatch.setattr(call_reasoner, "load_tool_config", lambda: config)

    class ToolModel:
        def bind_tools(self, tools):
            self.name = next(t.name for t in tools if t.name.startswith("mcp_"))
            return self

        async def ainvoke(self, messages):
            if not isinstance(messages[-1], ToolMessage):
                return AIMessage(
                    content="Private planning must not be published.",
                    tool_calls=[{"name": self.name, "args": {"text": "MCP works"}, "id": "mcp-1"}],
                )
            assert "MCP works" in messages[-1].content
            return AIMessage(content="MCP works.")

    reasoner = CallReasoner.__new__(CallReasoner)
    reasoner.response_model = ToolModel()
    reasoner.response_settings = ModelSettings()

    async def scenario():
        return "".join(
            [
                part
                async for part in reasoner.answer_stream("Use the configured MCP tool.", "", "", [])
            ]
        )

    assert run(scenario()) == "MCP works."


def test_completed_public_lookup_answers_without_starting_another_tool_loop(config, monkeypatch):
    from langchain_core.messages import AIMessage

    from app.agent import call_reasoner
    from app.agent.call_reasoner import CallReasoner

    monkeypatch.setattr(call_reasoner, "load_tool_config", lambda: config)

    class Model:
        def bind(self, **kwargs):
            return self

        def bind_tools(self, tools):
            raise AssertionError("The lookup has already completed; do not execute it twice")

        async def ainvoke(self, messages):
            assert any(
                isinstance(m, ToolMessage) and "verified metric" in m.content for m in messages
            )
            return AIMessage(content="The source reports the verified metric.")

    reasoner = CallReasoner.__new__(CallReasoner)
    reasoner.response_model = Model()
    reasoner.response_settings = ModelSettings()

    async def scenario():
        return "".join(
            [
                part
                async for part in reasoner.answer_stream(
                    "What is the annual revenue?",
                    "",
                    "",
                    [],
                    context={
                        "decision": "lookup",
                        "use_tools": True,
                        "tool_evidence": {
                            "name": "web_search",
                            "arguments": {"query": "revenue"},
                            "content": "A verified metric from the opened report.",
                        },
                    },
                )
            ]
        )

    assert run(scenario()) == "The source reports the verified metric."
