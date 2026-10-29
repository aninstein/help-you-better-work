import { useEffect, useState } from "react";

const key = "duet.auto-play-new-audio";

function readPreference() {
  try {
    return localStorage.getItem(key) !== "false";
  } catch {
    return true;
  }
}

/** Browser-wide preference shared by Talker and Hinter tabs on this origin. */
export function useAutoPlayback() {
  const [enabled, setEnabledState] = useState(readPreference);

  useEffect(() => {
    const sync = (event: StorageEvent) => {
      if (event.key === key) setEnabledState(event.newValue !== "false");
    };
    window.addEventListener("storage", sync);
    return () => window.removeEventListener("storage", sync);
  }, []);

  function setEnabled(value: boolean) {
    try {
      localStorage.setItem(key, String(value));
    } catch {
      // Storage may be unavailable in a private browser context.
    }
    setEnabledState(value);
  }

  return { autoPlaybackEnabled: enabled, setAutoPlaybackEnabled: setEnabled };
}
