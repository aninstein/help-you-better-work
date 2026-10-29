import { useEffect, useState } from "react";
import { getToken } from "../lib/api";
import { StreamPlayer } from "../lib/streamPlayer";

/** One audio subscription for new message replies; never requests microphone access. */
export function useReplyAudio(sessionId: string, enabled: boolean) {
  const [warning, setWarning] = useState("");
  useEffect(() => {
    if (!enabled) { setWarning(""); return; }
    let stopped = false;
    let socket: WebSocket | undefined;
    let reconnect: number | undefined;
    const context = new AudioContext();
    const player = new StreamPlayer(context, context.destination, (data) => {
      if (socket?.readyState === WebSocket.OPEN) socket.send(JSON.stringify(data));
    }, undefined, undefined, setWarning);
    const unlock = () => { void context.resume().then(() => setWarning("")).catch(() => {}); };
    window.addEventListener("pointerdown", unlock);
    window.addEventListener("keydown", unlock);
    const connect = () => {
      if (stopped) return;
      socket = new WebSocket(`${location.protocol === "https:" ? "wss:" : "ws:"}//${location.host}/api/v1/ws/sessions/${sessionId}/reply-audio`);
      socket.binaryType = "arraybuffer";
      socket.onopen = () => socket?.send(JSON.stringify({ token: getToken() }));
      socket.onmessage = ({ data }) => {
        if (data instanceof ArrayBuffer) player.frame(data);
        else {
          const event = JSON.parse(data);
          player.event(event);
          if (event.type === "audio.failed") setWarning(event.message);
        }
      };
      socket.onclose = () => {
        player.silence();
        if (!stopped) reconnect = window.setTimeout(connect, 2000);
      };
    };
    connect();
    return () => {
      stopped = true; window.clearTimeout(reconnect); socket?.close(); player.silence();
      window.removeEventListener("pointerdown", unlock); window.removeEventListener("keydown", unlock);
      void context.close();
    };
  }, [sessionId, enabled]);
  return warning;
}
