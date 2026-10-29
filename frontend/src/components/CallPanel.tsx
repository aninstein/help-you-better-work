import AssistanceBrief from "./AssistanceBrief";
import { useEffect, useRef, useState } from "react";
import { BellRing, LoaderCircle, Pause, Phone, Play, Send, Sparkles, Square } from "lucide-react";
import { post } from "../lib/api";
import type { Session, VoiceCall } from "../lib/types";
import GuidanceList from "./GuidanceList";

export const callLabels: Record<string, string> = {
  connecting: "正在接通", listening: "倾听中", thinking: "思考中",
  speaking: "倾听中 · AI 正在说话", reconnecting: "正在重新接通", ended: "通话已结束",
};

export function callPhaseLabel(call: VoiceCall, tick: number): string {
  if (call.paused) return "自动回答已暂停";
  if (call.activity_state === "thinking" && call.thinking_started_at) {
    const seconds = Math.min(10, Math.max(0, Math.floor((tick - Date.parse(call.thinking_started_at)) / 1000)));
    return `思考中 · ${seconds} / 10 秒`;
  }
  if (call.activity_state === "listening" && call.status === "thinking") return "倾听中";
  return callLabels[call.status] || "通话中";
}

export default function CallPanel({ session, refresh, notify }: {
  session: Session; refresh: () => Promise<void>; notify: (text: string) => void;
}) {
  const active = !!session.call && session.call.status !== "ended";
  const [hint, setHint] = useState("");
  const [terms, setTerms] = useState("");
  const [termsBusy, setTermsBusy] = useState(false);
  const [persistent, setPersistent] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [tick, setTick] = useState(Date.now());
  useEffect(() => {
    if (!active) return;
    const timer = window.setInterval(() => setTick(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [active]);
  useEffect(() => {
    setTerms((session.call?.recognition_context?.terms || ["API", "DAU", "HTTP", "SQL", "Kokoro"]).join(", "));
  }, [session.call?.id, session.call?.recognition_context?.version]);
  async function saveTerms() {
    setTermsBusy(true); setError("");
    try {
      await post(`/sessions/${session.id}/calls/${session.call!.id}/recognition-context`, {
        terms: terms.split(/[,，\n]/).map((term) => term.trim()).filter(Boolean),
      });
      await refresh(); notify("识别术语已保存，从下一段发言起生效");
    } catch (e) { setError((e as Error).message); }
    finally { setTermsBusy(false); }
  }
  async function intervene(action: string) {
    setBusy(true); setError("");
    try {
      await post(`/sessions/${session.id}/calls/${session.call!.id}/interventions`, { action, text: hint, persistent });
      setHint(""); await refresh();
      notify(action === "pause_auto" ? "自动回答已暂停" : action === "resume_auto" ? "已恢复自动回答" : action === "add_for_next" ? "提示仅对后续输入生效" : "提示已应用，原回答将更新");
    } catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  }
  if (session.role !== "hinter" || !active) return null;
  const elapsed = session.call ? Math.max(0, Math.floor((tick - Date.parse(session.call.started_at)) / 1000)) : 0;
  const duration = `${Math.floor(elapsed / 60).toString().padStart(2, "0")}:${(elapsed % 60).toString().padStart(2, "0")}`;
  return (
    <div className="call-panel">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex min-w-0 items-center gap-3">
          <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-talker/15 text-talker">
            <Phone size={18} />
          </div>
          <div>
            <p className="text-sm font-medium">AI 实时通话</p>
            <p className="mt-1 text-[11px] text-white/50" role="status">
              {callPhaseLabel(session.call!, tick)} · {duration}
            </p>
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2">
              <button className="secondary-button" disabled={busy} onClick={() => void intervene(session.call!.paused ? "resume_auto" : "pause_auto")}>
                {session.call!.paused ? <Play size={14} /> : <Pause size={14} />}{session.call!.paused ? "恢复回答" : "暂停自动回答"}
              </button>
              <button className="secondary-button !text-red-300" disabled={busy} onClick={() => void intervene("block_target")}><Square size={12} />别回答</button>
        </div>
      </div>
      <GuidanceList session={session} refresh={refresh} />
      {active && session.role === "hinter" && (
        <form noValidate className="mt-3" onSubmit={(e) => { e.preventDefault(); if (hint.trim()) void intervene("correct_now"); }}>
          <label className="field-label" htmlFor="call-hint">随时给 AI 提示</label>
          <textarea id="call-hint" className="field resize-none" rows={2} value={hint} onChange={(e) => setHint(e.target.value)} placeholder="例如：不要报未核实的数字；只说两句话。可直接粘贴背景文档。" />
          <p className="mt-2 text-[11px] text-white/45">仅后续生效不会改动旧答案；纠正并重答会更新原回答。</p>
          <div className="mt-3 flex flex-wrap items-center justify-between gap-3">
            <label className="flex items-center gap-2 text-[11px] text-white/55"><input type="checkbox" checked={persistent} onChange={(e) => setPersistent(e.target.checked)} />跨对话保留</label>
            <div className="flex flex-wrap gap-2">
              <button type="button" className="secondary-button" disabled={busy || !hint.trim()} onClick={() => void intervene("add_for_next")}>仅后续生效</button>
              <button type="submit" className="primary-button !bg-hinter" disabled={busy || !hint.trim()}>{busy ? <LoaderCircle size={14} className="animate-spin" /> : <Send size={14} />}纠正并重答</button>
            </div>
          </div>
        </form>
      )}
      <details className="mt-3 text-[11px] text-white/45">
        <summary className="cursor-pointer">识别术语</summary>
        <label htmlFor="call-recognition-terms" className="mt-2 block leading-5">明确授权给语音识别的产品名或缩写，用逗号分隔；请勿填客户数据或提示指令。</label>
        <div className="mt-2 flex gap-2">
          <input id="call-recognition-terms" className="field min-w-0 flex-1 !text-xs" value={terms} onChange={(e) => setTerms(e.target.value)} />
          <button type="button" className="secondary-button shrink-0" disabled={termsBusy} onClick={() => void saveTerms()}>{termsBusy ? "保存中…" : "保存"}</button>
        </div>
        <p className="mt-2">用于整段识别，从下一段发言生效。业务 Hint 仍在上方独立填写。</p>
      </details>
      {(error || session.call?.error) && <p className="mt-3 text-xs leading-5 text-orange-300" role="alert">{error || session.call?.error}</p>}
      {active && session.call?.last_thinking_ms != null && <p className="mt-2 text-[10px] text-white/35">最近一次准备回答 {(session.call.last_thinking_ms / 1000).toFixed(1)} 秒{session.call.last_voice_to_send_ms != null ? ` · 语音结束至首音发送 ${(session.call.last_voice_to_send_ms / 1000).toFixed(1)} 秒` : ""} · {session.call.pending_count || 0} 个问题待补充</p>}
    </div>
  );
}

export function CallReviewPanel({ session, refresh, notify }: {
  session: Session; refresh: () => Promise<void>; notify: (text: string) => void;
}) {
  const alerts = session.call?.alerts || [];
  const seen = useRef(new Set<string>());
  const sound = useRef<AudioContext | null>(null);
  const [soundOn, setSoundOn] = useState(false);
  const [hints, setHints] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState("");
  const submitting = useRef(false);
  const [error, setError] = useState("");
  useEffect(() => {
    const incoming = alerts.filter((a) => a.status === "open" && !seen.current.has(a.id));
    if (incoming.length) {
      notify(`${incoming.length} 个通话问题待处理`);
      if (sound.current?.state === "running") {
        const oscillator = sound.current.createOscillator();
        const gain = sound.current.createGain();
        gain.gain.setValueAtTime(.08, sound.current.currentTime);
        gain.gain.exponentialRampToValueAtTime(.001, sound.current.currentTime + .35);
        oscillator.frequency.value = 680;
        oscillator.connect(gain).connect(sound.current.destination);
        oscillator.start(); oscillator.stop(sound.current.currentTime + .35);
      }
    }
    alerts.forEach((a) => seen.current.add(a.id));
    const pending = alerts.filter((a) => ["open", "expired"].includes(a.status)).length;
    document.title = pending ? `(${pending}) 待处理 · Duet AI` : "Duet AI · 双通道语音协作";
  }, [alerts, notify]);
  useEffect(() => () => { void sound.current?.close(); document.title = "Duet AI · 双通道语音协作"; }, []);
  async function review(questionId: string, decision: "auto_answer" | "approve" | "reject") {
    const question = session.questions.find((q) => q.id === questionId);
    if (!question || submitting.current) return;
    submitting.current = true;
    setBusy(questionId); setError("");
    try {
      await post(`/sessions/${session.id}/calls/${session.call!.id}/reviews`, { question_id: questionId, version: question.version, decision, hint: decision === "approve" ? hints[questionId] || "" : "" });
      await refresh();
      notify(decision === "reject" ? "已跳过这个问题" : decision === "auto_answer" ? "已交给 AI，将直接回复并播报" : "已提交，AI 将按提示回答");
    } catch (e) { setError((e as Error).message); }
    finally { submitting.current = false; setBusy(""); }
  }
  const visible = alerts.filter((a) => a.status !== "resolved").slice().reverse();
  return (
    <div className="flex-1 overflow-y-auto p-5">
      <h2 className="flex items-center gap-2 text-sm"><BellRing size={16} className="text-hinter" />通话监管<span className="ml-auto text-xs text-hinter">{alerts.filter((a) => ["open", "expired"].includes(a.status)).length} 待处理</span></h2>
      <button className="secondary-button mt-3 !px-3 !py-1.5 !text-[11px]" aria-pressed={soundOn} onClick={async () => {
        if (sound.current) { await sound.current.close(); sound.current = null; setSoundOn(false); }
        else { sound.current = new AudioContext(); await sound.current.resume(); setSoundOn(true); }
      }}>{soundOn ? "关闭提醒声音" : "开启提醒声音"}</button>
      <p className="mt-2 text-xs leading-6 text-white/45">一段话可包含多个问题，普通问题先答。需要介入的问题单独保留，处理后会补答，不影响继续聊天。</p>
      {error && <p role="alert" className="mt-3 text-xs text-orange-300">{error}</p>}
      {!visible.length && <p className="mt-5 rounded-xl border border-dashed border-white/10 p-5 text-xs leading-6 text-white/40">当前没有需要介入的问题。左侧会持续显示 AI 已回答的内容。</p>}
      <div className="mt-4 space-y-3">{visible.map((alert) => (
        <article key={alert.id} className="rounded-xl border border-hinter/20 bg-hinter/[.03] p-3">
          <p className="text-[10px] text-hinter">{alert.status === "notice" ? "提醒 · 已自动处理" : alert.status === "denied" ? "风险请求已拒绝" : alert.status === "expired" ? "等待超时，仍可选择回复方式" : "待人工补充"}</p>
          <p className="mt-2 text-xs leading-6 text-white/85">{alert.text}</p>
          <p className="mt-2 text-[11px] leading-5 text-white/45">{alert.reason}</p>
          <AssistanceBrief request={session.questions.find(q => q.id === alert.question_id)?.assistance_request} />
          {!['denied', 'notice'].includes(alert.status) && <>
            <p id={`help-${alert.id}`} className="mt-2 text-[11px] leading-5 text-white/45">{session.call?.paused ? "自动回答已暂停，请先恢复回答。" : ["data_lookup", "human_evidence_required"].includes(alert.category) ? "请提供已核实的数据，AI 已向 Talker 说明当前无法核验。" : "无需填写提示，AI 将根据现有信息直接回复并播报。"}</p>
            <label htmlFor={`hint-${alert.id}`} className="field-label mt-3">{["data_lookup", "human_evidence_required"].includes(alert.category) ? "补充已核实的数据或说明" : "补充回答方向（可选）"}</label>
            <textarea id={`hint-${alert.id}`} className="field resize-none !text-xs" rows={2} aria-describedby={`help-${alert.id}`} disabled={!!busy} placeholder="可直接填写 200、50% 或补充说明，AI 会结合此题回答" value={hints[alert.question_id] || ""} onChange={(e) => setHints((h) => ({ ...h, [alert.question_id]: e.target.value }))} />
            <div className="mt-3 flex flex-wrap gap-2">
              <button className="primary-button flex-1 whitespace-nowrap !px-3" disabled={!!busy || session.call?.paused || alert.category === "human_evidence_required"} aria-busy={busy === alert.question_id} onClick={() => void review(alert.question_id, "auto_answer")}>
                {busy === alert.question_id ? <LoaderCircle size={14} className="animate-spin" /> : <Sparkles size={14} />}确认后回答
              </button>
              <button className="secondary-button flex-1 whitespace-nowrap !px-3" disabled={!!busy || session.call?.paused || (["data_lookup", "human_evidence_required"].includes(alert.category) && !hints[alert.question_id]?.trim())} onClick={() => void review(alert.question_id, "approve")}>按提示回答</button>
              <button className="secondary-button whitespace-nowrap !px-3" disabled={!!busy} onClick={() => void review(alert.question_id, "reject")}>跳过</button>
            </div>
          </>}
        </article>
      ))}</div>
    </div>
  );
}
