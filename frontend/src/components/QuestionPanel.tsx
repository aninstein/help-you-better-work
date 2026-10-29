import { useEffect, useState } from "react";
import {
  Check,
  CheckSquare2,
  Clock3,
  CopyCheck,
  Sparkles,
  Square,
  X,
} from "lucide-react";
import AssistanceBrief from "./AssistanceBrief";
import type { Question, QuestionStatus, Role } from "../lib/types";
import {
  isWaiting,
  sortQuestions,
  urgency,
  waitingLabel,
  waitingSeconds,
} from "../lib/questionTime";

const labels: Record<QuestionStatus, string> = {
  ready: "准备回答",
  legacy_pending: "历史未处理",
  awaiting_user: "等待澄清",
  unavailable: "暂未完成",
  pending_review: "历史未处理",
  generating: "正在回答",
  pending_confirmation: "待确认发送",
  answered: "已回答",
  rejected: "已拒绝",
  failed: "生成失败",
  blocked: "禁止回答",
  held: "待人工补充",
  expired: "等待超时",
  interrupted: "已中断",
  withdrawn: "已撤回",
};

export function QuestionBadge({ status, live = false }: { status: QuestionStatus; live?: boolean }) {
  const tone =
    status === "answered"
      ? "border-emerald-400/20 bg-emerald-400/10 text-emerald-300"
      : status === "generating"
        ? "border-ai/20 bg-ai/10 text-ai"
        : status === "failed"
          ? "border-red-300/20 bg-red-300/10 text-red-300"
          : "border-hinter/20 bg-hinter/10 text-hinter";
  return (
    <span
      className={`inline-flex h-[22px] shrink-0 items-center justify-center rounded-full border px-2 text-[10px] leading-none ${tone}`}
    >
      {live && status === "generating" ? "正在回答" : labels[status] || status}
    </span>
  );
}

export default function QuestionPanel({
  role,
  questions,
  selectedIds,
  onToggle,
  onSelectAll,
  onReject,
}: {
  role: Role;
  questions: Question[];
  selectedIds: string[];
  onToggle: (id: string) => void;
  onSelectAll: () => void;
  onReject: (id: string) => Promise<void>;
}) {
  const [filter, setFilter] = useState("all");
  const [now, setNow] = useState(Date.now());
  const [rejecting, setRejecting] = useState<string | null>(null);
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, []);
  const pending = questions.filter((q) => q.status === "held");
  const visible = sortQuestions(
    questions.filter((q) => filter === "all" || q.status === filter),
  );
  const allSelected =
    pending.length > 0 && pending.every((q) => selectedIds.includes(q.id));
  return (
    <div className="scrollbar-none flex-1 overflow-y-auto p-5">
      <div className="mb-4 flex items-start justify-between">
        <div>
          <h2 className="text-sm font-medium">AI-generated question list</h2>
          <p className="mt-1 text-[11px] text-white/40">
            等待最久的问题优先 · {questions.length} 个问题
          </p>
        </div>
        <CopyCheck size={17} className="text-ai" />
      </div>
      <div className="mb-4 flex flex-wrap gap-1 rounded-lg bg-black/20 p-1">
        {[
          ["all", "全部"],
          ["held", "待补充"],
          ["awaiting_user", "待澄清"],
          ["answered", "已回答"],
        ].map(([id, label]) => (
          <button
            key={id}
            onClick={() => setFilter(id)}
            className={`flex-1 rounded-md px-2 py-1.5 text-[11px] ${filter === id ? "bg-white/10 text-white/80" : "text-white/40"}`}
          >
            {label}
          </button>
        ))}
      </div>
      {role === "hinter" && (
        <button
          onClick={onSelectAll}
          className="mb-3 flex w-full items-center gap-2 rounded-lg border border-white/10 px-3 py-2 text-left text-[11px] text-white/55"
        >
          {allSelected ? (
            <CheckSquare2 size={14} className="text-hinter" />
          ) : (
            <Square size={14} />
          )}
          选择全部待补充问题<span className="ml-auto">{pending.length}</span>
        </button>
      )}
      <div className="space-y-2">
        {visible.map((question) => {
          const selected = selectedIds.includes(question.id);
          const waiting = isWaiting(question);
          const selectable = role === "hinter" && ["held", "unavailable", "awaiting_user", "legacy_pending"].includes(question.status);
          const level = urgency(question, now);
          const border =
            level === "red"
              ? "border-red-400/60 bg-red-400/[.08]"
              : level === "orange"
                ? "border-orange-400/50 bg-orange-400/[.07]"
                : selected && selectable
                  ? "border-hinter/40 bg-hinter/5"
                  : "border-white/10 bg-white/[.02]";
          return (
            <div
              key={question.id}
              data-question-id={question.id}
              data-urgency={level}
              className={`rounded-xl border ${border}`}
            >
              <button
                disabled={!selectable}
                onClick={() => onToggle(question.id)}
                className="w-full p-3 text-left transition enabled:hover:bg-white/5"
              >
                <div className="mb-2 flex items-center gap-2">
                  {role === "hinter" && (
                    <span
                      className={`flex h-4 w-4 items-center justify-center rounded border ${selected && selectable ? "border-hinter bg-hinter text-ink" : "border-white/20 text-transparent"}`}
                    >
                      <Check size={10} />
                    </span>
                  )}
                  <span className="text-[10px] text-white/40">
                    {question.display_no}
                  </span>
                  <span className="ml-auto">
                    <QuestionBadge status={question.status} live={!!question.call_id} />
                  </span>
                </div>
                <p className="text-xs leading-5 text-white/70">
                  {question.text}
                </p>
                {selectable && <AssistanceBrief request={question.assistance_request} />}
                {question.duplicate_count > 1 && (
                  <p className="mt-2 text-[10px] text-hinter">
                    合并 {question.duplicate_count} 次重复表达
                  </p>
                )}
              </button>
              {waiting && (
                <div className="flex items-center justify-between gap-2 border-t border-white/[.06] px-3 py-2">
                  <span
                    data-testid="question-wait"
                    className={`flex items-center gap-1.5 text-[11px] ${level === "red" ? "text-red-300" : level === "orange" ? "text-orange-300" : "text-white/45"}`}
                  >
                    <Clock3 size={12} />
                    等待 {waitingLabel(waitingSeconds(question, now))}
                    {level === "red"
                      ? " · 超过6分钟"
                      : level === "orange"
                        ? " · 超过3分钟"
                        : ""}
                  </span>
                  {role === "hinter" && (
                    <button
                      disabled={rejecting === question.id}
                      aria-label={`拒绝回答 ${question.display_no}`}
                      onClick={async () => {
                        setRejecting(question.id);
                        try {
                          await onReject(question.id);
                        } finally {
                          setRejecting(null);
                        }
                      }}
                      className="flex items-center gap-1 rounded-md px-2 py-1 text-[10px] text-white/50 hover:bg-red-400/10 hover:text-red-300 disabled:opacity-40"
                    >
                      <X size={11} />
                      拒绝回答
                    </button>
                  )}
                </div>
              )}
            </div>
          );
        })}
      </div>
      {!visible.length && (
        <p className="rounded-xl border border-dashed border-white/10 p-6 text-center text-xs leading-6 text-white/35">
          {questions.length
            ? "这个分类还没有问题。"
            : "Talker 发送消息后，AI 识别的问题将在这里出现。"}
        </p>
      )}
      <div className="mt-5 rounded-xl border border-ai/15 bg-ai/[.03] p-3 text-[11px] leading-5 text-white/40">
        <Sparkles className="mb-2 text-ai" size={14} />
        普通问题自动回答；只有明确的人工规则或缺少已核实依据时需要补充。提示可随时发送，AI 会在后续回答中遵守。
      </div>
    </div>
  );
}
