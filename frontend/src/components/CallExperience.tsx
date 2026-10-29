import { useEffect, useRef } from "react";
import { createPortal } from "react-dom";
import { ArrowUpRight, Mic, MicOff, Minimize2, PhoneOff } from "lucide-react";

export type CallPhase = "listening" | "thinking" | "connecting" | "paused" | "speaking";

/** Audio is owned by useVoiceCall. Changing the visual never recreates media. */
export function CallSignal({ phase, level, compact = false }: {
  phase: CallPhase; level: number; compact?: boolean;
}) {
  const contour = useRef<SVGPathElement>(null);
  const halo = useRef<SVGPathElement>(null);
  const ticks = useRef<SVGPathElement>(null);
  const volume = useRef(level);
  useEffect(() => { volume.current = level; }, [level]);
  useEffect(() => {
    const motion = window.matchMedia("(prefers-reduced-motion: reduce)");
    let frame = 0;
    let last = 0;
    let amplitude = 0;
    const draw = (now: number) => {
      if (now - last < 30 && !motion.matches && phase === "listening") {
        frame = requestAnimationFrame(draw);
        return;
      }
      last = now;
      const target = phase === "listening" ? Math.min(1, Math.sqrt(Math.max(0, volume.current)) * 2.4) : 0;
      amplitude += (target - amplitude) * .22;
      const time = motion.matches ? 0 : now / 1000;
      let path = "";
      let rays = "";
      if (compact) {
        for (let x = 8; x <= 152; x += 2) {
          const envelope = Math.sin((x - 8) / 144 * Math.PI) ** 1.2;
          const wave = Math.sin(x * .13 - time * 10) * .65 + Math.sin(x * .28 + time * 7) * .35;
          const y = 24 + (motion.matches ? 0 : wave * envelope * amplitude * 17);
          path += `${x === 8 ? "M" : "L"}${x},${y.toFixed(2)} `;
        }
      } else {
        for (let i = 0; i <= 144; i++) {
          const angle = i / 144 * Math.PI * 2 - Math.PI / 2;
          const wave = Math.sin(angle * 9 + time * 7) * .5 + Math.sin(angle * 17 - time * 11) * .3 + Math.cos(angle * 25 + time * 4) * .2;
          const radius = 108 + (motion.matches ? 0 : wave * amplitude * 12);
          const x = 160 + Math.cos(angle) * radius;
          const y = 160 + Math.sin(angle) * radius;
          path += `${i === 0 ? "M" : "L"}${x.toFixed(2)},${y.toFixed(2)} `;
          if (i < 144) {
            const outer = radius + 3 + (motion.matches ? 0 : Math.abs(wave) * amplitude * 14);
            rays += `M${(160 + Math.cos(angle) * (radius + 2)).toFixed(2)},${(160 + Math.sin(angle) * (radius + 2)).toFixed(2)} L${(160 + Math.cos(angle) * outer).toFixed(2)},${(160 + Math.sin(angle) * outer).toFixed(2)} `;
          }
        }
        path += "Z";
      }
      contour.current?.setAttribute("d", path);
      halo.current?.setAttribute("d", path);
      ticks.current?.setAttribute("d", rays);
      if (!motion.matches && phase === "listening") frame = requestAnimationFrame(draw);
    };
    const restart = () => { cancelAnimationFrame(frame); last = 0; frame = requestAnimationFrame(draw); };
    restart();
    motion.addEventListener("change", restart);
    return () => { cancelAnimationFrame(frame); motion.removeEventListener("change", restart); };
  }, [phase, compact]);
  return (
    <div aria-hidden="true" className={`${compact ? "call-signal-line" : "call-orbit"} is-${phase}`}>
      <svg viewBox={compact ? "0 0 160 48" : "0 0 320 320"} fill="none">
        {!compact && <circle cx="160" cy="160" r="108" className="call-orbit-base" />}
        <g className="call-signal-shape">
          <path ref={halo} className="call-signal-halo" />
          <path ref={contour} className="call-signal-contour" />
          {!compact && <path ref={ticks} className="call-signal-ticks" />}
        </g>
      </svg>
    </div>
  );
}

export interface CallExperienceProps {
  container: HTMLElement | null;
  expanded: boolean;
  phase: CallPhase;
  label: string;
  duration: string;
  level: number;
  muted: boolean;
  connected: boolean;
  ending: boolean;
  caption: string;
  warning: string;
  error: string;
  pendingCount: number;
  pendingLabel?: string;
  onMinimize: () => void;
  onExpand: () => void;
  onMute: () => void;
  onHangup: () => void;
}

export default function CallExperience(props: CallExperienceProps) {
  const { container, expanded, phase, label, duration, level, muted, connected, ending,
    caption, warning, error, pendingCount, pendingLabel = "个问题待补充", onMinimize, onExpand, onMute, onHangup } = props;
  const minimize = useRef<HTMLButtonElement>(null);
  const expand = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    // The surface is a non-modal region; the sidebar remains available.
    const frame = requestAnimationFrame(() => {
      (expanded ? minimize.current : expand.current)?.focus({ preventScroll: true });
    });
    return () => cancelAnimationFrame(frame);
  }, [expanded, container]);
  const controls = (
    <div className={expanded ? "call-stage-controls" : "call-dock-controls"}>
      <button type="button" className="call-control" disabled={!connected || ending}
        onClick={onMute} aria-pressed={muted} aria-label={muted ? "取消静音" : "静音"}>
        {muted ? <MicOff size={20} /> : <Mic size={20} />}
        <span>{muted ? "取消静音" : "静音"}</span>
      </button>
      <button type="button" className="call-control call-control-end" disabled={ending}
        onClick={onHangup} aria-label={ending ? "正在挂断" : "挂断通话"} aria-busy={ending}>
        <PhoneOff size={21} /><span>{ending ? "正在挂断" : "挂断"}</span>
      </button>
    </div>
  );
  if (!expanded || !container) return (
    <section className="call-dock" aria-label="已缩小的语音通话">
      <button ref={expand} type="button" className="call-dock-expand" onClick={onExpand} aria-label="展开通话">
        <CallSignal compact phase={phase} level={level} />
        <span className="call-dock-label"><span role="status">{label}</span><span className="call-duration">{duration}</span></span>
        <ArrowUpRight size={15} className="call-expand-icon" />
      </button>
      {controls}
      {(caption || error || warning) && <p className={`call-dock-caption ${error || warning ? "has-warning" : ""}`} role={error ? "alert" : undefined}>{error || warning || caption}</p>}
      {!!pendingCount && <p className="call-pending">{pendingCount} {pendingLabel}</p>}
    </section>
  );
  return createPortal(
    <section className={`call-stage is-${phase}`} aria-label="语音通话" onKeyDown={(event) => {
      if (event.key === "Escape") { event.preventDefault(); onMinimize(); }
    }}>
      <header className="call-stage-header">
        <div className="call-stage-heading"><span className="call-live-dot" /><span>语音通话</span><span className="call-duration">{duration}</span></div>
        <button ref={minimize} type="button" className="call-minimize" onClick={onMinimize} aria-label="缩小通话"><Minimize2 size={17} /><span>缩小</span></button>
      </header>
      <div className="call-stage-center">
        <CallSignal phase={phase} level={level} />
        <p className="call-stage-status" role="status">{label}</p>
        <p className="call-stage-hint">{phase === "connecting" ? "正在连接音频…" : muted ? "取消静音后继续说话" : phase === "thinking" ? "随时开口，我会继续听" : phase === "paused" ? "仍在倾听，等待恢复自动回答" : "直接开口说话，结束后自然停顿"}</p>
        <div className="call-stage-caption"><p>{caption}</p></div>
      </div>
      <footer className="call-stage-footer">
        <div className="call-stage-feedback">
          {(error || warning) && <p role={error ? "alert" : "status"}>{error || warning}</p>}
          {!!pendingCount && <p className="call-pending">{pendingCount} {pendingLabel}，可以继续说话</p>}
        </div>
        {controls}
        <p className="call-stage-footnote">{connected ? (muted ? "麦克风已静音 · AI 仍可继续回答" : "麦克风持续倾听 · 可随时打断") : "接通后即可开始说话"}</p>
      </footer>
    </section>, container,
  );
}
