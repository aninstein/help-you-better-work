import { useEffect, useState } from "react";
import { ArrowRight, AudioLines, BookOpen, Keyboard, LoaderCircle, MessageSquare, Plus, UserRound } from "lucide-react";
import { api, login, post } from "../lib/api";
import type { Role, Talker } from "../lib/types";

export default function RoleSelection({ onSelect }: { onSelect: (role: Role) => void }) {
  const [talkers, setTalkers] = useState<Talker[]>([]);
  const [loading, setLoading] = useState(true);
  const [creating, setCreating] = useState(false);
  const [showCreate, setShowCreate] = useState(false);
  const [name, setName] = useState("");
  const [error, setError] = useState("");
  useEffect(() => {
    let active = true;
    const load = async () => {
      try {
        await login("hinter");
        if (!active) return;
        const data = await api<{ items: Talker[] }>("/talkers");
        if (active) { setTalkers(data.items); setError(""); }
      } catch (e) { if (active) setError((e as Error).message); }
      finally { if (active) setLoading(false); }
    };
    void load();
    const timer = window.setInterval(load, 10000);
    return () => { active = false; window.clearInterval(timer); };
  }, []);
  function enter(role: Role, talker?: Talker) {
    const params = new URLSearchParams({ role });
    if (talker) { params.set("talker", talker.id); if (talker.latest_session_id) params.set("session", talker.latest_session_id); }
    history.replaceState(null, "", "/?" + params);
    onSelect(role);
  }
  async function create() {
    if (!name.trim() || creating) return;
    setCreating(true); setError("");
    try {
      const talker = await post<Talker>("/talkers", { name: name.trim() });
      setTalkers(items => [...items, talker]); setName(""); setShowCreate(false);
    } catch (e) { setError((e as Error).message); }
    finally { setCreating(false); }
  }
  return <div className="grid-lines min-h-screen px-5 py-8 sm:px-10">
    <div className="mx-auto max-w-6xl">
      <header className="flex items-center gap-3 border-b border-white/10 pb-6">
        <AudioLines size={26} className="text-ai" /><div><p className="text-sm font-semibold tracking-widest">DUET AI</p><p className="mt-1 text-xs text-white/40">Personal assistant · Shared intelligence</p></div>
      </header>
      <section className="pb-9 pt-12 sm:pt-16">
        <p className="mb-4 text-xs tracking-[.18em] text-talker">TALKER WORKSPACES</p>
        <h1 className="text-3xl font-semibold tracking-tight sm:text-5xl">每个人，都有自己的助手空间。</h1>
        <p className="mt-5 max-w-2xl text-sm leading-7 text-white/50">选择一个 Talker，继续对话与通话。每个空间拥有独立的会话和长期记忆，Hinter 在统一工作台中协助。</p>
      </section>
      <div className="grid items-start gap-6 lg:grid-cols-[minmax(0,1fr)_300px]">
        <section className="min-w-0 rounded-2xl border border-white/10 bg-ink/80">
          <div className="flex flex-wrap items-center justify-between gap-3 border-b border-white/10 p-5">
            <h2 className="text-base font-medium">Talker 空间 <span className="ml-2 text-xs text-white/35">{talkers.length}</span></h2>
            <button className="secondary-button !border-talker/25 !text-talker" onClick={() => setShowCreate(value => !value)} disabled={loading} aria-expanded={showCreate}><Plus size={15} />创建 Talker</button>
          </div>
          {showCreate && <form onSubmit={event => { event.preventDefault(); void create(); }} className="flex flex-wrap items-end gap-3 border-b border-white/10 bg-talker/[.03] p-5">
            <label className="min-w-[160px] flex-1 text-xs text-white/60">Talker 名称<input className="field mt-2" autoFocus required maxLength={80} value={name} onChange={event => setName(event.target.value)} placeholder="例如：Alex、产品讨论" disabled={creating} /></label>
            <button className="primary-button !bg-talker" type="submit" disabled={!name.trim() || creating}>{creating ? <LoaderCircle size={15} className="animate-spin" /> : <Plus size={15} />}创建空间</button>
            <button className="secondary-button" type="button" onClick={() => setShowCreate(false)} disabled={creating}>取消</button>
          </form>}
          {loading ? <div className="flex items-center gap-3 p-8 text-sm text-white/45"><LoaderCircle size={18} className="animate-spin" />正在加载空间…</div> :
            <ul className="divide-y divide-white/[.07]">{talkers.map(talker => <li key={talker.id}>
              <button onClick={() => enter("talker", talker)} className="group flex w-full items-center gap-4 p-5 text-left transition hover:bg-talker/5" aria-label={`进入 ${talker.name} 的空间`}>
                <span className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl border border-talker/20 bg-talker/10 text-talker"><UserRound size={20} /></span>
                <span className="min-w-0 flex-1"><span className="block truncate text-sm font-medium">{talker.name}</span><span className="mt-2 flex flex-wrap gap-3 text-[11px] text-white/40"><span className="flex items-center gap-1"><MessageSquare size={12} />{talker.session_count} 个会话</span><span className="flex items-center gap-1"><BookOpen size={12} />独立 Wiki</span>{talker.sessions.some(s => s.call_active) && <span className="text-talker">通话中</span>}</span></span>
                <ArrowRight size={18} className="shrink-0 text-white/30 transition group-hover:translate-x-1 group-hover:text-talker" />
              </button>
            </li>)}</ul>}
          {!loading && !talkers.length && !error && <p className="p-8 text-sm text-white/45">创建第一个 Talker，开始你的对话。</p>}
          {error && <p role="alert" className="border-t border-white/10 p-5 text-sm text-red-300">{error}</p>}
        </section>
        <aside className="rounded-2xl border border-hinter/20 bg-hinter/[.035] p-6">
          <Keyboard size={24} className="mb-6 text-hinter" /><h2 className="text-lg font-medium">Hinter 工作台</h2>
          <p className="mb-7 mt-3 text-sm leading-7 text-white/50">一个协作入口，连接所有 Talker。选择会话，查看对话、补充提示，维护对方的记忆。</p>
          <button className="secondary-button w-full !justify-between !border-hinter/25 !text-hinter" onClick={() => enter("hinter")}>进入 Hinter<ArrowRight size={16} /></button>
        </aside>
      </div>
    </div>
  </div>;
}
