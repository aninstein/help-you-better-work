import type { Question } from "./types";

export function isWaiting(question: Question) {
  return [
    "held",
  ].includes(question.status);
}

export function waitingSeconds(question: Question, now: number) {
  const start = Date.parse(question.created_at);
  return Number.isFinite(start)
    ? Math.max(0, Math.floor((now - start) / 1000))
    : 0;
}

export function urgency(question: Question, now: number) {
  if (!isWaiting(question)) return "none";
  const seconds = waitingSeconds(question, now);
  return seconds >= 360 ? "red" : seconds >= 180 ? "orange" : "normal";
}

export function waitingLabel(seconds: number) {
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
}

export function sortQuestions(questions: Question[]) {
  return [...questions].sort((a, b) => {
    const group = Number(isWaiting(b)) - Number(isWaiting(a));
    return (
      group ||
      Date.parse(a.created_at) - Date.parse(b.created_at) ||
      a.display_no.localeCompare(b.display_no)
    );
  });
}
