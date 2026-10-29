import { useState } from "react";
import { LoaderCircle, Send, X } from "lucide-react";
import { post } from "../lib/api";
import type { Question, Session } from "../lib/types";
import AssistanceBrief from "./AssistanceBrief";
import GuidanceList from "./GuidanceList";

export default function ReviewComposer({ session, selected, onClear, refresh, notify }: {
  session: Session; selected: Question[]; onClear: () => void;
  refresh: () => Promise<void>; notify: (text: string) => void;
}) {
  const [hint, setHint] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [reference, setReference] = useState(false);
  const pending = session.questions.filter(q => q.status === "held");
  const [draftTarget, setDraftTarget] = useState<{ selected: Question[]; candidate?: Question; multiple: boolean } | null>(null);
  const targets = selected.length ? selected : draftTarget?.selected || [];
  const briefQuestions = targets.length ? targets : draftTarget?.candidate ? [draftTarget.candidate] : pending.length === 1 ? pending : [];
  function clearSelection() { setDraftTarget(null); onClear(); }
  async function send() {
    if (targets.length > 1 && /^[+\-]?\s*[$€£¥]?\s*\d[\d,.]*(?:\s*(?:%|percent|million|billion|thousand|[kmb]|万|亿|个|人|元|倍))?$/i.test(hint.trim())) {
      setError("这个数值对应哪一道题？请只选择对应的问题后补充。");
      return;
    }
    setBusy(true); setError("");
    try {
      if (targets.length) {
        for (const q of targets) await post(`/sessions/${session.id}/questions/${q.id}/resolve`, {
          hint, version: q.version,
        });
      } else await post(`/sessions/${session.id}/guidance`, { text: hint, kind: reference ? "reference" : "instruction",
        value_question_id: draftTarget?.candidate?.id,
        require_explicit_target: draftTarget?.multiple,
      });
      setHint(""); setDraftTarget(null); onClear(); await refresh();
      notify(targets.length ? "信息已保存，AI 将补充回答原问题" : "提示已保存并生效，AI 会持续遵守");
    } catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  }
  return (
    <form noValidate className="rounded-2xl border border-hinter/20 bg-hinter/[.035] p-4"
      onSubmit={(e) => { e.preventDefault(); if (hint.trim() || targets.length) void send(); }}>
      <div className="mb-3 flex items-center justify-between gap-2 text-[11px] text-hinter">
        <span>{targets.length ? `为 ${targets.map((q) => q.display_no).join("、")} 补充信息` : "随时给 AI 提示"}</span>
        {targets.length > 0 && <button type="button" className="icon-button !h-7 !w-7" onClick={clearSelection} aria-label="清除问题选择"><X size={13} /></button>}
      </div>
      {briefQuestions.map(question => <div key={question.id} className="mb-3 rounded-lg bg-black/10 p-3">
        <p className="text-[10px] text-hinter">{question.display_no} · {targets.length ? "正在为此题补充" : "当前待补充的问题"}</p>
        <AssistanceBrief request={question.assistance_request} />
        {!targets.length && <p className="mt-1 text-[10px] text-white/40">直接填写数值会用于此题；也可以发送完整的会话提示。</p>}
      </div>)}
      <label htmlFor="message-guidance" className="sr-only">Hinter 私有提示</label>
      <textarea id="message-guidance" value={hint} onChange={(e) => {
        if (!hint.trim()) setDraftTarget({ selected: [...selected], candidate: pending.length === 1 ? pending[0] : undefined, multiple: pending.length > 1 });
        setHint(e.target.value);
      }} rows={2}
        placeholder="中文提示、已核实信息或禁止事项。无需等问题，也无需逐条审核。"
        onKeyDown={(e) => { if (e.nativeEvent.isComposing) return; if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) { e.preventDefault(); if (hint.trim() && !busy) void send(); } }}
        className="min-h-14 w-full resize-none bg-transparent text-sm leading-6 text-white/80 placeholder:text-white/30 focus:outline-none" />
      {!targets.length && <label className="mt-2 flex items-center gap-2 text-[10px] text-white/45">
        <input type="checkbox" checked={reference} onChange={(e) => setReference(e.target.checked)} />
        作为参考资料保存{hint.length > 6000 ? " · 长文将自动按资料处理" : ""}
      </label>}
      <div className="mt-3 flex items-center justify-between gap-3">
        <span className="text-[10px] leading-5 text-white/40">仅 Hinter 可见 · {targets.length ? "只关联所选问题" : "本会话持续生效"}</span>
        <button type="submit" disabled={busy || (!hint.trim() && !targets.length)} className="primary-button !bg-hinter">
          {busy ? <LoaderCircle size={14} className="animate-spin" /> : <Send size={14} />}
          {targets.length ? "补充回答" : "发送提示"}
        </button>
      </div>
      {error && <p role="alert" className="mt-2 text-xs text-orange-300">{error}</p>}
      {session.guidance_projection === "sync_pending" && <p role="status" className="mt-2 text-[10px] text-white/45">提示已生效，Wiki 正在同步。</p>}
      <GuidanceList session={session} refresh={refresh} />
    </form>
  );
}
