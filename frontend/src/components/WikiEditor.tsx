import { useCallback, useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import {
  Download,
  FileText,
  LoaderCircle,
  Plus,
  Save,
  Trash2,
  X,
} from "lucide-react";
import { api } from "../lib/api";
import ClearWikiDialog from "./ClearWikiDialog";
import type { Session, WikiDocument } from "../lib/types";

export default function WikiEditor({
  session,
  refresh,
  onClose,
  initialPath = "context.md",
}: {
  session: Session;
  refresh: () => Promise<void>;
  onClose: () => void;
  initialPath?: string;
}) {
  const loadVersion = useRef(0);
  const [newPath, setNewPath] = useState<string | null>(null);
  const [path, setPath] = useState("context.md");
  const [document, setDocument] = useState<WikiDocument | null>(null);
  const [content, setContent] = useState("");
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [preview, setPreview] = useState(false);
  const [clearing, setClearing] = useState(false);
  const load = useCallback(async (name: string) => {
    const version = ++loadVersion.current;
    setBusy(true);
    setMessage("");
    try {
      const data = await api<WikiDocument>(
        `/wiki/document?session_id=${session.id}&path=${encodeURIComponent(name)}`,
      );
      if (version !== loadVersion.current) return;
      setDocument(data);
      setContent(data.content);
      setPath(name);
      setDirty(false);
    } catch (e) {
      if (version === loadVersion.current) setMessage((e as Error).message);
    } finally {
      if (version === loadVersion.current) setBusy(false);
    }
  }, [session.id]);
  useEffect(() => {
    void load(initialPath);
    return () => { loadVersion.current++; };
  }, [load, initialPath]);
  async function save() {
    setBusy(true);
    setMessage("");
    try {
      const data = await api<WikiDocument>(
        `/wiki/document?session_id=${session.id}&path=${encodeURIComponent(path)}`,
        {
          method: "PUT",
          body: JSON.stringify({ content, revision: document?.revision }),
        },
      );
      setDocument(data);
      setDirty(false);
      setMessage("文档已保存，Agent 将读取更新后的内容。");
      await refresh();
    } catch (e) {
      setMessage((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  function newDocument() {
    const name = newPath?.trim();
    if (!name) { setNewPath("notes/new-note.md"); return; }
    if (!name.endsWith(".md")) { setMessage("请使用 .md 文档路径。"); return; }
    loadVersion.current++;
    setBusy(false);
    setNewPath(null);
    setPath(name);
    setDocument(null);
    setContent("# 新文档\n\n");
    setDirty(true);
    setMessage("保存后请在 context.md 中添加链接。");
  }
  async function remove() {
    try {
      await api(`/wiki/document?session_id=${session.id}&path=${encodeURIComponent(path)}`, {
        method: "DELETE",
      });
      await refresh();
      await load("context.md");
    } catch (e) {
      setMessage((e as Error).message);
    }
  }
  function download() {
    const url = URL.createObjectURL(
      new Blob([content], { type: "text/markdown" }),
    );
    const anchor = window.document.createElement("a");
    anchor.href = url;
    anchor.download = path.split("/").pop()!;
    anchor.click();
    URL.revokeObjectURL(url);
  }
  function navigate(name: string) {
    if (dirty) {
      setMessage("当前内容尚未保存，请先保存再切换文档。");
      return;
    }
    void load(name);
  }
  return (
    <div
      className="fixed inset-0 z-50 flex flex-col bg-[#0c0f18]"
      role="dialog"
      aria-modal="true"
      aria-label="个人 Wiki 编辑页面"
    >
      <header className="flex shrink-0 flex-wrap items-center justify-between gap-3 border-b border-white/10 px-5 py-4">
        <div className="flex items-center gap-3">
          <FileText size={22} className="text-ai" />
          <div>
            <h2 className="text-base font-medium">
              {session.talker_name} 的 LLM Wiki
            </h2>
            <p className="mt-1 text-xs text-white/40">
              此 Talker 跨会话共享 · context.md 为中心
            </p>
          </div>
        </div>
        <div className="flex gap-2">
          {session.role === "hinter" && (
            <button
              className="secondary-button !border-red-400/25 !text-red-300"
              aria-label="删除所有 Wiki 信息"
              onClick={() => setClearing(true)}
            >
              <Trash2 size={14} />
              删除全部 Wiki
            </button>
          )}
          <button onClick={newDocument} className="secondary-button">
            <Plus size={14} />
            新建文档
          </button>
          <button onClick={download} className="secondary-button">
            <Download size={14} />
            下载 Markdown
          </button>
          <button
            onClick={() => {
              if (dirty) setMessage("当前内容尚未保存，请先保存。");
              else onClose();
            }}
            className="icon-button"
            aria-label="关闭 Wiki 编辑页面"
          >
            <X size={20} />
          </button>
        </div>
      </header>
      {newPath !== null && <form className="flex flex-wrap gap-2 border-b border-white/10 p-4" onSubmit={event => { event.preventDefault(); newDocument(); }}>
        <label className="flex min-w-0 flex-1 items-center gap-3 text-sm">文档路径<input className="field" aria-label="新文档路径" autoFocus value={newPath} onChange={event => setNewPath(event.target.value)} /></label>
        <button className="primary-button" type="submit">创建文档</button><button className="secondary-button" type="button" onClick={() => setNewPath(null)}>取消</button>
      </form>}
      {clearing && (
        <ClearWikiDialog
          session={session}
          onClose={() => setClearing(false)}
          onCleared={async () => {
            await refresh();
            await load("context.md");
            setDirty(false);
          }}
        />
      )}
      <div className="grid min-h-0 flex-1 md:grid-cols-[300px_minmax(0,1fr)]">
        <aside className="flex min-h-0 flex-col border-r border-white/10">
          <p className="border-b border-white/10 p-4 text-xs leading-6 text-white/45">
            标题、长短期记忆、主题和链接都可以自由编辑，Agent 通过 Prompt
            归纳内容。
          </p>
          <div className="flex-1 space-y-2 overflow-y-auto p-4 max-md:max-h-[24vh]">
            {session.wiki.documents.map((item) => (
              <button
                key={item.path}
                className={`w-full rounded-xl border p-3 text-left ${path === item.path ? "border-ai/40 bg-ai/10" : "border-white/10 bg-white/[.02]"}`}
                onClick={() => navigate(item.path)}
              >
                <div className="truncate text-xs text-white/80">
                  {item.title || item.path}
                </div>
                <div className="mt-2 truncate font-mono text-[10px] text-white/35">
                  {item.path}
                </div>
              </button>
            ))}
          </div>
        </aside>
        <section className="flex min-h-0 flex-col p-4 sm:p-7">
          <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
            <h3 className="font-mono text-sm text-ai">
              {path}
              {dirty && (
                <span className="ml-2 text-xs text-hinter">未保存</span>
              )}
            </h3>
            <div className="flex gap-2">
              <button
                className="secondary-button"
                onClick={() => setPreview(!preview)}
              >
                {preview ? "编辑" : "阅读"}
              </button>
              <button
                className="secondary-button"
                disabled={busy || dirty}
                onClick={() => load(path)}
              >
                重新加载
              </button>
              {path !== "context.md" && (
                <button
                  className="icon-button"
                  aria-label="删除 Wiki 文档"
                  onClick={remove}
                >
                  <Trash2 size={15} />
                </button>
              )}
              <button
                className="primary-button"
                disabled={busy || !dirty}
                onClick={save}
              >
                {busy ? (
                  <LoaderCircle size={14} className="animate-spin" />
                ) : (
                  <Save size={14} />
                )}
                保存文档
              </button>
            </div>
          </div>
          {preview ? (
            <div className="flex-1 overflow-y-auto rounded-2xl border border-white/10 bg-black/15 p-5">
              <WikiText content={content} onLink={name => navigate(path.startsWith("private/") && !name.startsWith("private/") ? "private/" + name : name)} />
            </div>
          ) : (
            <textarea
              aria-label="编辑 Wiki 文档内容"
              className="min-h-[260px] flex-1 resize-none rounded-2xl border border-white/10 bg-black/20 p-5 font-mono text-sm leading-7 text-white/80 focus:border-ai/40 focus:outline-none"
              value={content}
              onChange={(e) => {
                setContent(e.target.value);
                setDirty(true);
              }}
              spellCheck={false}
            />
          )}
          {message && (
            <p
              role="status"
              className="mt-4 rounded-xl border border-white/10 p-3 text-sm text-white/70"
            >
              {message}
            </p>
          )}
        </section>
      </div>
    </div>
  );
}

export function WikiText({
  content,
  onLink,
}: {
  content: string;
  onLink?: (name: string) => void;
}) {
  return (
    <div className="markdown-content">
      <ReactMarkdown
        components={{
          a: ({ href, children }) =>
            href && onLink && !/^(https?:|mailto:|#)/.test(href) ? (
              <button
                onClick={() => onLink(href)}
                className="text-talker underline underline-offset-4"
              >
                {children}
              </button>
            ) : (
              <a href={href} target="_blank" rel="noreferrer">
                {children}
              </a>
            ),
        }}
      >
        {content}
      </ReactMarkdown>
    </div>
  );
}
