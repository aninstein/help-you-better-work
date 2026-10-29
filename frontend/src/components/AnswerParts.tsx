import { useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import type { AnswerPart } from "../lib/types";
import { post } from "../lib/api";

const statuses: Record<AnswerPart["status"], string> = {
  known: "已找到", partial: "部分信息", unknown: "未知",
  clarify: "待澄清", refused: "无法回答", withdrawn: "已撤回",
};

/** Shared item rendering and correction for one stable caller-turn answer. */
export default function AnswerParts({ parts, sessionId, answerId, canCorrect, refresh, notify }: {
  parts: AnswerPart[]; sessionId: string; answerId: string; canCorrect: boolean;
  refresh: () => Promise<void>; notify: (text: string) => void;
}) {
  const [editing, setEditing] = useState("");
  const [value, setValue] = useState("");
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [error, setError] = useState("");
  const [baseAnswer, setBaseAnswer] = useState("");
  const [saving, setSaving] = useState(false);
  const field = useRef<HTMLTextAreaElement>(null);
  const opener = useRef<HTMLButtonElement | null>(null);
  function close() {
    setDrafts((current) => { const next = { ...current }; delete next[editing]; return next; });
    setEditing(""); setValue(""); setError(""); opener.current?.focus();
  }
  async function save(partId: string) {
    if (saving) return;
    if (!value.trim()) { setError("请填写这项数据或纠正内容。"); field.current?.focus(); return; }
    setSaving(true); setError("");
    try {
      await post(`/sessions/${sessionId}/answers/${encodeURIComponent(answerId)}/parts/${encodeURIComponent(partId)}`, { text: value, expected_answer: baseAnswer });
      close(); await refresh(); notify("补充已收到，将更新原回答中的这一项");
    } catch (e) { setError((e as Error).message); field.current?.focus(); }
    finally { setSaving(false); }
  }
  return <ol className="list-none space-y-4" aria-label="逐项回答">
    {parts.map((part) => {
      const fieldId = `answer-part-${part.id.replace(/[^a-zA-Z0-9_-]/g, "-")}`;
      return <li key={part.id} className="min-w-0 border-b border-white/10 pb-4 last:border-0 last:pb-0">
        <div className="mb-1.5 flex flex-wrap items-start gap-2">
          <h3 className="min-w-0 flex-1 break-words text-sm font-medium text-white/85">{part.question}</h3>
          <span className={`shrink-0 rounded border px-1.5 py-0.5 text-[10px] ${part.status === "unknown" || part.status === "refused" ? "border-hinter/20 text-hinter" : "border-white/15 text-white/50"}`}>
            {part.updated && ["known", "partial"].includes(part.status) ? "已更新" : statuses[part.status]}
          </span>
          {canCorrect && !["refused", "withdrawn"].includes(part.status) && <button type="button"
            className="shrink-0 rounded px-1 text-[11px] text-hinter/80 hover:text-hinter focus-visible:outline focus-visible:outline-2 focus-visible:outline-hinter disabled:opacity-40"
            disabled={saving} aria-expanded={editing === part.id} aria-controls={`${fieldId}-form`}
            onClick={(event) => {
              opener.current = event.currentTarget;
              setEditing(part.id); setValue(drafts[part.id] || ""); setBaseAnswer(part.answer); setError("");
              requestAnimationFrame(() => field.current?.focus());
            }}>{part.status === "unknown" ? "补充" : "纠正"}</button>}
        </div>
        <div className={`markdown-content break-words text-sm leading-6 ${part.status === "unknown" ? "text-white/50" : "text-white/80"}`}>
          <ReactMarkdown>{part.answer}</ReactMarkdown>
        </div>
        {canCorrect && editing === part.id && <form id={`${fieldId}-form`} noValidate className="mt-3 space-y-2"
          onSubmit={(event) => { event.preventDefault(); void save(part.id); }}>
          <label className="field-label" htmlFor={fieldId}>补充或纠正此项</label>
          <textarea id={fieldId} ref={field} rows={3} className="field resize-none" value={value}
            disabled={saving} aria-invalid={!!error} aria-describedby={error ? `${fieldId}-error` : undefined}
            onChange={(event) => {
              setValue(event.target.value);
              setDrafts((current) => ({ ...current, [part.id]: event.target.value }));
            }} placeholder="填写数值、单位、统计期间或正确口径" />
          {error && <p id={`${fieldId}-error`} role="alert" className="text-xs text-orange-300">{error}</p>}
          <div className="flex flex-wrap justify-end gap-2">
            <button type="button" className="secondary-button" disabled={saving} onClick={close}>取消</button>
            <button type="submit" className="primary-button min-w-24" disabled={saving} aria-busy={saving}>{saving ? "提交中…" : "更新原回答"}</button>
          </div>
        </form>}
      </li>;
    })}
  </ol>;
}
