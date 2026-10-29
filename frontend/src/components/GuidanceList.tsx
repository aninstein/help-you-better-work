import { useState } from "react";
import { post } from "../lib/api";
import type { Session } from "../lib/types";

export default function GuidanceList({ session, refresh }: { session: Session; refresh: () => Promise<void> }) {
  const [editing, setEditing] = useState("");
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const items = session.guidance?.filter((g) => !g.revoked) || [];
  if (!items.length) return null;
  async function save(id: string, revoke = false) {
    const item = items.find((g) => g.id === id);
    if (!item) return;
    setBusy(true); setError("");
    try {
      await post(`/sessions/${session.id}/guidance`, { text: revoke ? item.text : text,
        guidance_id: id, expected_revision: item.revision, revoke });
      setEditing(""); await refresh();
    } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  }
  return <details className="mt-3 text-[11px] text-white/45">
    <summary className="cursor-pointer">生效提示 · {items.length}</summary>
    <div className="mt-2 max-h-48 space-y-2 overflow-y-auto">
      {items.slice().reverse().map((item) => <div key={item.id} className="rounded-lg border border-white/10 p-2">
        {editing === item.id ? <>
          <textarea aria-label="修改已保存提示" value={text} onChange={(e) => setText(e.target.value)} rows={3} className="field resize-none !text-xs" />
          <div className="mt-2 flex gap-2">
            <button type="button" className="secondary-button !py-1" disabled={busy || !text.trim()} onClick={() => void save(item.id)}>保存修改</button>
            <button type="button" className="secondary-button !py-1" disabled={busy} onClick={() => setEditing("")}>取消</button>
          </div>
        </> : <>
          {item.binding && <p className="mb-1 text-[10px] leading-5 text-hinter/70">{item.question_id ? "对应问题" : "参考问题"}：{item.binding.extracted_question}</p>}
          <p className="max-h-24 overflow-y-auto whitespace-pre-wrap break-words leading-5">{item.text}</p>
          <div className="mt-1 flex gap-3">
            <button type="button" className="text-hinter hover:text-white" disabled={busy} onClick={() => { setEditing(item.id); setText(item.text); }}>修改</button>
            <button type="button" className="hover:text-white" disabled={busy} onClick={() => void save(item.id, true)}>撤销提示</button>
          </div>
        </>}
      </div>)}
    </div>
    {error && <p role="alert" className="mt-2 text-xs text-orange-300">{error}</p>}
  </details>;
}
