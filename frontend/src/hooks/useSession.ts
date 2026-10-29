import { useCallback, useEffect, useRef, useState } from "react";
import { api, getToken, login, post } from "../lib/api";
import type { Role, Session, Talker } from "../lib/types";

export interface SessionEvent {
  eventId: string; type: string; sequence: number; occurredAt: string;
  resourceId?: string; audioId?: string;
}

export function useSession(role: Role) {
  const params = new URLSearchParams(location.search);
  const [selection, setSelection] = useState({ id: params.get("session") || "", talkerId: params.get("talker") || "", revision: 0 });
  const [session, setSession] = useState<Session | null>(null);
  const [connection, setConnection] = useState("connecting");
  const [error, setError] = useState("");
  const [lastEvent, setLastEvent] = useState<SessionEvent | null>(null);
  const current = useRef<Session | null>(null);
  const epoch = useRef(0);
  const apply = useCallback((snapshot: Session) => {
    if (!current.current || (snapshot.id === current.current.id && snapshot.last_sequence >= current.current.last_sequence)) {
      current.current = snapshot; setSession(snapshot);
    }
  }, []);
  const selectSession = useCallback((id: string, talkerId?: string) => {
    epoch.current++;
    current.current = null; setSession(null); setLastEvent(null); setError(""); setConnection("connecting");
    setSelection(previous => ({ id, talkerId: talkerId || previous.talkerId, revision: previous.revision + 1 }));
  }, []);
  const refresh = useCallback(async () => {
    const id = current.current?.id, version = epoch.current;
    if (!id) return;
    const snapshot = await api<Session>(`/sessions/${id}`);
    if (version === epoch.current && current.current?.id === id) apply(snapshot);
  }, [apply]);

  useEffect(() => {
    const version = ++epoch.current;
    let stopped = false, socket: WebSocket | undefined, events: EventSource | undefined, retry: number | undefined, failures = 0;
    const active = () => !stopped && version === epoch.current;
    const receive = (event: MessageEvent, id: string) => {
      if (!active()) return;
      const update = JSON.parse(event.data);
      if (update.type === "session.deleted") {
        if (role === "hinter") {
          void api<{ items: Talker[] }>("/talkers").then(({ items }) => {
            if (!active()) return;
            const next = items.find(talker => talker.latest_session_id);
            if (next?.latest_session_id) selectSession(next.latest_session_id, next.id);
            else location.assign("/");
          }).catch(() => { if (active()) location.assign("/"); });
        } else location.assign("/");
        return;
      }
      if (update.data && update.data.id !== id) {
        if (role === "talker") selectSession(update.data.id, update.data.talker_id);
        return;
      }
      if (update.event_id) setLastEvent({ eventId: update.event_id, type: update.type, sequence: update.sequence, occurredAt: update.occurred_at, resourceId: update.resource_id, audioId: update.audio_id });
      if (update.data) apply(update.data);
    };
    const connect = async () => {
      try {
        await login(role, selection.talkerId || undefined);
        if (!active()) return;
        const snapshot = await api<Session>(selection.id ? `/sessions/${selection.id}` : `/sessions/default${selection.talkerId ? `?talker_id=${encodeURIComponent(selection.talkerId)}` : ""}`);
        if (!active()) return;
        if (role === "talker" && snapshot.talker_id !== selection.talkerId) await login(role, snapshot.talker_id);
        if (!active()) return;
        apply(snapshot);
        const url = new URLSearchParams(location.search);
        url.set("role", role); url.set("session", snapshot.id); url.set("talker", snapshot.talker_id);
        history.replaceState(null, "", "/?" + url);
        setError("");
        socket = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/api/v1/ws/sessions/${snapshot.id}?token=${encodeURIComponent(getToken())}&after_sequence=${current.current?.last_sequence || 0}`);
        socket.onopen = () => { if (active()) { failures = 0; setConnection("live"); } };
        socket.onmessage = event => receive(event, snapshot.id);
        socket.onclose = () => {
          if (!active()) return;
          setConnection("reconnecting"); failures++;
          if (failures < 2) { retry = window.setTimeout(connect, 1500); return; }
          events = new EventSource(`/api/v1/sessions/${snapshot.id}/events?token=${encodeURIComponent(getToken())}&after_sequence=${current.current?.last_sequence || 0}`);
          events.onopen = () => { if (active()) setConnection("sse"); };
          events.onmessage = event => receive(event, snapshot.id);
          events.onerror = () => { events?.close(); if (active()) retry = window.setTimeout(connect, 2000); };
        };
      } catch (e) {
        if (active()) { setError((e as Error).message); setConnection("offline"); retry = window.setTimeout(connect, 3000); }
      }
    };
    void connect();
    return () => { stopped = true; socket?.close(); events?.close(); window.clearTimeout(retry); };
  }, [role, selection, apply, selectSession]);

  async function newSession(customerId: string, customerName: string, language: string) {
    const previous = current.current, version = epoch.current;
    const created = await post<Session>(previous ? `/sessions/${previous.id}/reset` : "/sessions", { talker_id: previous?.talker_id || selection.talkerId, customer_id: customerId, customer_name: customerName, language });
    if (version === epoch.current) selectSession(created.id, created.talker_id);
  }
  async function resetSession() {
    const previous = current.current;
    if (previous) await newSession(previous.customer_id, previous.customer_name, "en-US");
  }
  return { session, connection, error, refresh, lastEvent, newSession, selectSession, resetSession,
    selectedSessionId: session?.id || selection.id, selectedTalkerId: session?.talker_id || selection.talkerId };
}
