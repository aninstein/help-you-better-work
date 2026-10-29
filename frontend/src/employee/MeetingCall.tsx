import { useEffect, useState } from 'react';
import CallExperience from '../components/CallExperience';
import type { CallPhase } from '../components/CallExperience';
import type { useMeetingVoice } from './useMeetingVoice';

type Props = {
  call: ReturnType<typeof useMeetingVoice>; container: HTMLElement | null;
  thinking: boolean; preparingSpeech: boolean; playing: boolean; pending: number;
  reply?: string; failed?: boolean; compact?: boolean; onHangup: () => void;
};

export default function MeetingCall({ call, container, thinking, preparingSpeech, playing, pending, reply = '', failed = false, compact = false, onHangup }: Props) {
  const [expanded, setExpanded] = useState(false), [tick, setTick] = useState(Date.now());
  useEffect(() => {
    if (!call.present) { setExpanded(false); return; }
    const timer = setInterval(() => setTick(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [call.present]);
  useEffect(() => { if (compact) setExpanded(false); }, [compact]);
  // The owner sets inert on the covered chat content; minimizing never touches media.
  useEffect(() => {
    const content = container?.parentElement?.querySelector<HTMLElement>('.employee-chat-content');
    if (content) content.inert = call.present && expanded;
    return () => { if (content) content.inert = false; };
  }, [container, expanded, call.present]);
  if (!call.present) return null;
  const elapsed = call.startedAt ? Math.max(0, Math.floor((tick - call.startedAt) / 1000)) : 0;
  const duration = `${Math.floor(elapsed / 60).toString().padStart(2, '0')}:${(elapsed % 60).toString().padStart(2, '0')}`;
  let phase: CallPhase = 'listening', label = '通话中 · 倾听中';
  if (call.state === 'connecting') { phase = 'connecting'; label = '正在连接通话'; }
  else if (call.userSpeaking && !call.muted) { phase = 'listening'; label = '通话中 · 倾听中'; }
  else if (playing) { phase = 'speaking'; label = 'Duet 正在说话'; }
  else if (call.acknowledging) { phase = 'speaking'; label = 'Duet 已收到'; }
  else if (call.state === 'transcribing') { phase = 'thinking'; label = '正在转写语音'; }
  else if (preparingSpeech) { phase = 'thinking'; label = '正在准备语音回复'; }
  else if (thinking) { phase = 'thinking'; label = 'Duet 正在思考'; }
  else if (call.muted) { phase = 'paused'; label = '通话中 · 麦克风已静音'; }
  return <CallExperience container={container} expanded={expanded} phase={phase} label={label}
    duration={duration} level={call.muted ? 0 : call.level} muted={call.muted} connected={call.active}
    ending={false} caption={call.acknowledging ? call.acknowledgementText : (playing || preparingSpeech) && reply ? reply : call.caption} error="" warning={call.warning || (call.provider==='local'?'当前使用本地识别；配置火山语音 Key 后可启用实时字幕。':failed ? '这条任务未完成，可缩小通话查看记录并重试。' : '')} pendingCount={pending}
    pendingLabel="个任务处理中" onMinimize={() => setExpanded(false)} onExpand={() => setExpanded(true)}
    onMute={call.toggleMute} onHangup={onHangup} />;
}
