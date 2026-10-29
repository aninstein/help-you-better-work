import { useEffect, useRef, useState } from "react";

interface Recognition {
  lang: string;
  continuous: boolean;
  interimResults: boolean;
  onresult:
    | ((event: {
        results: {
          length: number;
          [key: number]: {
            isFinal: boolean;
            [key: number]: { transcript: string };
          };
        };
      }) => void)
    | null;
  onerror: (() => void) | null;
  start: () => void;
  stop: () => void;
}

export function useRecorder(language: string) {
  const [recording, setRecording] = useState(false);
  const [transcript, setTranscript] = useState("");
  const [blob, setBlob] = useState<Blob | null>(null);
  const [error, setError] = useState("");
  const recorder = useRef<MediaRecorder | null>(null);
  const recognition = useRef<Recognition | null>(null);
  const stream = useRef<MediaStream | null>(null);
  const chunks = useRef<Blob[]>([]);

  useEffect(
    () => () => {
      recognition.current?.stop();
      if (recorder.current?.state === "recording") recorder.current.stop();
      stream.current?.getTracks().forEach((track) => track.stop());
    },
    [],
  );

  async function start() {
    try {
      setError("");
      setTranscript("");
      setBlob(null);
      stream.current = await navigator.mediaDevices.getUserMedia({
        audio: true,
      });
      recorder.current = new MediaRecorder(stream.current);
      chunks.current = [];
      recorder.current.ondataavailable = (event) => {
        if (event.data.size) chunks.current.push(event.data);
      };
      recorder.current.onstop = () => {
        setBlob(
          new Blob(chunks.current, {
            type: recorder.current?.mimeType || "audio/webm",
          }),
        );
        stream.current?.getTracks().forEach((track) => track.stop());
      };
      recorder.current.start();
      setRecording(true);
      const browser = window as unknown as {
        SpeechRecognition?: new () => Recognition;
        webkitSpeechRecognition?: new () => Recognition;
      };
      const Engine =
        browser.SpeechRecognition || browser.webkitSpeechRecognition;
      if (Engine) {
        recognition.current = new Engine();
        recognition.current.lang = language;
        recognition.current.continuous = true;
        recognition.current.interimResults = true;
        recognition.current.onresult = (event) => {
          let text = "";
          for (let i = 0; i < event.results.length; i++)
            text += event.results[i][0].transcript + " ";
          setTranscript(text.trim());
        };
        recognition.current.onerror = () =>
          setError(
            "浏览器转录暂不可用；录音结束后可填写转录，或由后端 STT 处理。",
          );
        try {
          recognition.current.start();
        } catch {
          /* Raw recording remains usable. */
        }
      }
    } catch {
      setError("无法访问麦克风，请检查浏览器权限，或改用文本输入。");
    }
  }
  function stop() {
    recognition.current?.stop();
    recorder.current?.stop();
    setRecording(false);
  }
  function reset() {
    setBlob(null);
    setTranscript("");
    setError("");
  }
  return {
    recording,
    transcript,
    setTranscript,
    blob,
    error,
    start,
    stop,
    reset,
  };
}
