import { useEffect, useRef, useState, type CSSProperties } from "react";
import { LoaderCircle, Pause, Play, RotateCcw, Volume2 } from "lucide-react";
import { post } from "../lib/api";
import {
  autoPlayed,
  cancelAudioGesture,
  claimAudio,
  sharedPlayer,
  waitForAudioGesture,
} from "../lib/audio";
import type { AudioAsset } from "../lib/types";

interface Props {
  audio: AudioAsset;
  sessionId: string;
  text: string;
  language: string;
  autoPlayToken?: string;
  original?: boolean;
  compact?: boolean;
}

export default function AudioControl({
  audio,
  sessionId,
  text,
  autoPlayToken,
  original,
  compact = false,
}: Props) {
  const [playing, setPlaying] = useState(false);
  const [progress, setProgress] = useState(0);
  const [blocked, setBlocked] = useState(false);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [duration, setDuration] = useState(0);
  const url = useRef<string | null>(null);
  const alive = useRef(true);
  const busy = useRef(false);
  const automaticToken = useRef<string | null>(null);
  const playRef = useRef<
    (restart?: boolean, automatic?: boolean, token?: string) => Promise<void>
  >(async () => {});

  async function play(restart = false, automatic = false, token?: string) {
    if (busy.current) return;
    const active = sharedPlayer.dataset.assetId === audio.id;
    if (!automatic && !restart && active && !sharedPlayer.paused) {
      sharedPlayer.pause();
      return;
    }
    setError("");
    if (audio.status === "browser") {
      if (!restart && speechSynthesis.speaking && !automatic) {
        if (speechSynthesis.paused) {
          speechSynthesis.resume();
          setPlaying(true);
        } else {
          speechSynthesis.pause();
          setPlaying(false);
        }
        return;
      }
      speechSynthesis.cancel();
      sharedPlayer.dataset.speechAssetId = audio.id;
      const speech = new SpeechSynthesisUtterance(audio.spoken_text || text);
      speech.lang = "en-US";
      speech.rate = 0.95;
      const voice =
        speechSynthesis.getVoices().find((v) => v.lang === "en-US") ||
        speechSynthesis.getVoices().find((v) => v.lang.startsWith("en"));
      if (voice) speech.voice = voice;
      speech.onstart = () => {
        setPlaying(true);
        setBlocked(false);
        if (token) autoPlayed.add(token);
      };
      speech.onend = () => {
        setPlaying(false);
        setProgress(100);
      };
      speech.onerror = () => {
        setPlaying(false);
        setBlocked(true);
        if (automatic)
          waitForAudioGesture(token || audio.id, () => {
            void playRef.current(false, true, token);
          });
      };
      speechSynthesis.speak(speech);
      return;
    }
    busy.current = true;
    setLoading(true);
    try {
      if (!url.current) {
        const grant = await post<{ playback_url: string }>(
          `/sessions/${sessionId}/audio/${audio.id}/playback`,
        );
        url.current = grant.playback_url;
      }
      if (!alive.current) return;
      const player = claimAudio(audio.id, url.current);
      if (restart || player.ended) player.currentTime = 0;
      await player.play();
      setBlocked(false);
      if (token) autoPlayed.add(token);
      cancelAudioGesture(token || audio.id);
    } catch (e) {
      if ((e as Error).name === "NotAllowedError") {
        setBlocked(true);
        if (automatic)
          waitForAudioGesture(token || audio.id, () => {
            void playRef.current(false, true, token);
          });
      } else setError((e as Error).message || "音频播放失败，请重试。");
    } finally {
      busy.current = false;
      if (alive.current) setLoading(false);
    }
  }
  playRef.current = play;

  useEffect(() => {
    alive.current = true;
    const update = () => {
      const active = sharedPlayer.dataset.assetId === audio.id;
      setPlaying(active && !sharedPlayer.paused && !sharedPlayer.ended);
      if (active) {
        setDuration(
          Number.isFinite(sharedPlayer.duration) ? sharedPlayer.duration : 0,
        );
        setProgress(
          sharedPlayer.duration
            ? (sharedPlayer.currentTime / sharedPlayer.duration) * 100
            : 0,
        );
      }
    };
    const failed = () => {
      if (sharedPlayer.dataset.assetId === audio.id) {
        url.current = null;
        setPlaying(false);
        setError("音频读取失败，请点击重试。");
      }
    };
    const events = [
      "play",
      "pause",
      "ended",
      "timeupdate",
      "loadedmetadata",
      "assetchange",
    ];
    events.forEach((event) => sharedPlayer.addEventListener(event, update));
    sharedPlayer.addEventListener("error", failed);
    return () => {
      alive.current = false;
      cancelAudioGesture(audio.id);
      if (automaticToken.current) cancelAudioGesture(automaticToken.current);
      events.forEach((event) =>
        sharedPlayer.removeEventListener(event, update),
      );
      sharedPlayer.removeEventListener("error", failed);
      if (sharedPlayer.dataset.assetId === audio.id) sharedPlayer.pause();
      if (sharedPlayer.dataset.speechAssetId === audio.id) {
        speechSynthesis.cancel();
        delete sharedPlayer.dataset.speechAssetId;
      }
    };
  }, [audio.id]);

  useEffect(() => {
    if (
      autoPlayToken &&
      ["ready", "browser"].includes(audio.status) &&
      !autoPlayed.has(autoPlayToken) &&
      automaticToken.current !== autoPlayToken
    ) {
      automaticToken.current = autoPlayToken;
      void playRef.current(false, true, autoPlayToken);
    }
  }, [audio.status, autoPlayToken]);

  if (!["ready", "browser"].includes(audio.status))
    return (
      <div className={`${compact ? "mt-2 text-[10px]" : "mt-3 border-t border-white/10 pt-3 text-xs"} flex items-center gap-2 text-white/40`}>
        <LoaderCircle size={compact ? 12 : 13} className="animate-spin text-ai" />
        正在生成英文语音…
      </div>
    );
  return (
    <div className={compact ? "mt-2" : "mt-3 border-t border-white/10 pt-3"}>
      <div className={`flex items-center ${compact ? "gap-2" : "gap-3"}`}>
        <button
          className={`flex shrink-0 items-center justify-center rounded-full ${compact ? "h-6 w-6" : "h-9 w-9"} ${original ? "bg-talker/15 text-talker" : "bg-ai/15 text-ai"}`}
          aria-label={
            playing
              ? "暂停语音"
              : original
                ? "播放用户原始语音"
                : "播放 AI 语音回答"
          }
          onClick={() => void play()}
        >
          {loading ? (
            <LoaderCircle size={compact ? 13 : 16} className="animate-spin" />
          ) : playing ? (
            <Pause size={compact ? 13 : 16} />
          ) : (
            <Play size={compact ? 13 : 16} />
          )}
        </button>
        <div className="min-w-0 flex-1">
          <div className={`${compact ? "mb-1 text-[9px]" : "mb-2 text-[10px]"} flex justify-between text-white/45`}>
            <span>
              {blocked
                ? "自动播放被阻止 · 点击播放"
                : playing
                  ? "正在播放…"
                  : original
                    ? "原始录音 · 点击回听"
                    : "English voice · 点击播放"}
            </span>
            <span>
              {duration ? (
                `${Math.round((progress * duration) / 100)} / ${Math.round(duration)}s`
              ) : (
                <Volume2 size={12} />
              )}
            </span>
          </div>
          <input
            aria-label="音频进度"
            type="range"
            min="0"
            max="100"
            value={progress}
            style={{ "--range-progress": `${progress}%` } as CSSProperties}
            onChange={(e) => {
              if (
                sharedPlayer.dataset.assetId === audio.id &&
                sharedPlayer.duration
              )
                sharedPlayer.currentTime =
                  (Number(e.target.value) / 100) * sharedPlayer.duration;
              setProgress(Number(e.target.value));
            }}
            className={`audio-range w-full ${compact ? `audio-range-compact ${original ? "is-talker" : "is-ai"}` : ""}`}
          />
        </div>
        <button
          className={`icon-button ${compact ? "!h-6 !w-6 !rounded-md" : ""}`}
          onClick={() => void play(true)}
          aria-label="从头重播"
        >
          <RotateCcw size={compact ? 13 : 15} />
        </button>
      </div>
      {error && (
        <p role="alert" className="mt-2 text-xs text-orange-300">
          {error}
        </p>
      )}
    </div>
  );
}
