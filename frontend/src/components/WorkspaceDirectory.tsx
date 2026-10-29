import { useCallback, useEffect, useRef, useState } from "react";
import { ArrowLeft, ChevronDown, ChevronRight, FolderTree, LoaderCircle, MessageSquare, Plus, RefreshCw, Trash2, UserRound } from "lucide-react";
import { api, login, post } from "../lib/api";
import type { Session, Talker } from "../lib/types";
import DeleteTalkerDialog from "./DeleteTalkerDialog";

export default function WorkspaceDirectory({ selectedTalkerId, selectedSessionId, onSelect, onExit, ready }: {
  selectedTalkerId: string; selectedSessionId: string;
  onSelect: (id: string, talkerId?: string) => void; onExit: () => void; ready: boolean;
}) {
  const [items, setItems] = useState<Talker[]>([]);
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [deleting, setDeleting] = useState<Talker | null>(null);
  const [notice, setNotice] = useState("");
  const loadVersion = useRef(0);
  const load = useCallback(async (signal?: AbortSignal) => {
    const version = ++loadVersion.current;
    try {
      const data = await api<{ items: Talker[] }>("/talkers", { signal });
      if (!signal?.aborted && version === loadVersion.current) { setItems(data.items); setError(""); }
    } catch (e) { if (!signal?.aborted && version === loadVersion.current) setError((e as Error).message); }
    finally { if (!signal?.aborted && version === loadVersion.current) setLoading(false); }
  }, []);
  useEffect(() => {
    const controller = new AbortController();
    void login("hinter").then(() => { if (!controller.signal.aborted) void load(controller.signal); }).catch(e => { if (!controller.signal.aborted) { setError((e as Error).message); setLoading(false); } });
    const timer = window.setInterval(() => { void load(controller.signal); }, 5000);
    return () => { controller.abort(); window.clearInterval(timer); };
  }, [load]);
  useEffect(() => { if (ready) void load(); }, [load, ready, selectedSessionId]);
  useEffect(() => { if (selectedTalkerId) setExpanded(previous => ({ ...previous, [selectedTalkerId]: true })); }, [selectedTalkerId]);
  async function createSession(talker: Talker) {
    if (busy) return;
    setBusy(talker.id);
    try {
      const session = await post<Session>("/sessions", { talker_id: talker.id, customer_name: talker.name });
      await load(); onSelect(session.id, talker.id); setOpen(false);
    } catch (e) { setError((e as Error).message); }
    finally { setBusy(""); }
  }
  function select(id: string, owner: string) { onSelect(id, owner); setOpen(false); }
  return <aside className="workspace-directory border-white/10 bg-[#0d1019]">
    <div className="flex h-[76px] shrink-0 items-center justify-between gap-2 border-b border-white/10 px-4">
      <button className="icon-button" aria-label="返回首页" onClick={onExit}><ArrowLeft size={17} /></button>
      <span className="flex flex-1 items-center gap-2 text-sm text-hinter"><FolderTree size={17} />Talker 目录</span>
      <button className="icon-button lg:hidden" aria-label="展开或折叠 Talker 目录" aria-expanded={open} aria-controls="talker-directory" onClick={() => setOpen(value => !value)}><ChevronDown size={17} className={open ? "rotate-180" : ""} /></button>
      <button className="icon-button" aria-label="刷新目录" disabled={loading} onClick={() => { void load(); }}><RefreshCw size={14} /></button>
    </div>
    <nav id="talker-directory" aria-label="Talker 和 Session 目录" className={`${open ? "block" : "hidden"} min-h-0 flex-1 overflow-y-auto p-3 lg:block`}>
      <p className="px-2 pb-4 pt-2 text-[11px] leading-5 text-white/35">选择 Talker，再选择要协助的会话。Wiki 随 Talker 加载。</p>
      {loading && <p className="flex items-center gap-2 p-3 text-xs text-white/45"><LoaderCircle className="animate-spin" size={14} />加载目录…</p>}
      {error && <p role="alert" className="p-2 text-xs leading-5 text-red-300">{error}</p>}
      {notice && <p role="status" className="p-2 text-xs leading-5 text-white/60">{notice}</p>}
      {!loading && !items.length && <p className="p-2 text-xs leading-6 text-white/45">暂无 Talker，可返回首页创建。</p>}
      <ul className="space-y-3">{items.map(talker => {
        const isExpanded = expanded[talker.id] ?? talker.id === selectedTalkerId;
        return <li key={talker.id}>
          <div className={`flex items-center rounded-lg ${selectedTalkerId === talker.id ? "bg-talker/10 text-talker" : "text-white/65 hover:bg-white/5"}`}>
            <button className="flex h-10 w-7 shrink-0 items-center justify-center" aria-label={`${isExpanded ? "折叠" : "展开"} ${talker.name}`} aria-expanded={isExpanded} onClick={() => setExpanded(previous => ({ ...previous, [talker.id]: !isExpanded }))}>{isExpanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}</button>
            <button className="flex min-w-0 flex-1 items-center gap-2 py-3 pr-2 text-left text-xs" onClick={() => { setExpanded(previous => ({ ...previous, [talker.id]: true })); if (talker.latest_session_id) select(talker.latest_session_id, talker.id); else void createSession(talker); }} aria-label={`选择 ${talker.name}`}><UserRound size={14} className="shrink-0" /><span className="truncate">{talker.name}</span><span className="ml-auto text-[10px] opacity-50">{talker.session_count}</span></button>
            <button className="icon-button !h-8 !w-8 !text-red-300/65 hover:!bg-red-400/10 hover:!text-red-300" aria-label={`删除 ${talker.name}`} title="删除 Talker 及其 Wiki" disabled={!!busy} onClick={() => setDeleting(talker)}><Trash2 size={14} /></button>
          </div>
          {isExpanded && <ul className="ml-4 mt-1 space-y-1 border-l border-white/10 pl-2">{talker.sessions.map((session, index) => <li key={session.id}>
            <button onClick={() => select(session.id, talker.id)} aria-current={session.id === selectedSessionId ? "page" : undefined} className={`flex w-full items-start gap-2 rounded-lg px-2 py-3 text-left ${session.id === selectedSessionId ? "bg-ai/15 text-ai ring-1 ring-inset ring-ai/20" : "text-white/50 hover:bg-white/5"}`}>
              <MessageSquare size={13} className="mt-0.5 shrink-0" /><span className="min-w-0 flex-1"><span className="block text-xs">会话 {talker.sessions.length - index}<span className="ml-2 text-[10px] opacity-50">{session.id.slice(0, 6)}</span></span><span className="mt-1 block text-[10px] opacity-60">{new Date(session.created_at).toLocaleString("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" })}</span><span className="mt-1 flex flex-wrap gap-2 text-[10px] opacity-70"><span>{session.call_active ? "● 通话中" : session.status === "ended" ? "已结束" : "进行中"}</span>{session.pending_count > 0 && <span className="text-hinter">{session.pending_count} 待补充</span>}</span></span>
            </button>
          </li>)}<li><button className="flex w-full items-center gap-2 rounded-lg px-2 py-2.5 text-[11px] text-white/40 hover:bg-white/5 hover:text-white/70 disabled:opacity-40" disabled={!!busy} onClick={() => { void createSession(talker); }}>{busy === talker.id ? <LoaderCircle size={12} className="animate-spin" /> : <Plus size={12} />}新建会话</button></li></ul>}
        </li>;
      })}</ul>
    </nav>
    <p className="hidden shrink-0 border-t border-white/10 p-4 text-[10px] text-white/30 lg:block">Hinter · 统一协作工作台</p>
    {deleting && <DeleteTalkerDialog talker={deleting} onClose={() => setDeleting(null)} onDeleted={(id, remaining) => {
      loadVersion.current++;
      setItems(remaining); setDeleting(null); setError(""); setLoading(false);
      setNotice("Talker 及其会话、Wiki 已删除。");
      if (id === selectedTalkerId) {
        const next = remaining.find(talker => talker.latest_session_id);
        if (next?.latest_session_id) onSelect(next.latest_session_id, next.id);
        else onExit();
      }
    }} />}
  </aside>;
}
