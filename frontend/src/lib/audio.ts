const silentWav =
  "data:audio/wav;base64,UklGRsQAAABXQVZFZm10IBAAAAABAAEAQB8AAIA+AAACABAAZGF0YaAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA";
const player = document.createElement("audio");
player.id = "duet-audio-player";
player.preload = "auto";
player.hidden = true;
document.body.appendChild(player);
let unlocked = false;
let unlocking = false;
const pending = new Map<string, () => void>();

// Unlock one reusable player in the user's click/keypress, before async events.
function unlock() {
  if (!unlocked && !unlocking && !player.dataset.assetId) {
    unlocking = true;
    player.src = silentWav;
    void player
      .play()
      .then(() => {
        unlocked = true;
        if (!player.dataset.assetId) player.pause();
      })
      .catch(() => {})
      .finally(() => {
        unlocking = false;
      });
  }
  for (const retry of pending.values()) retry();
  pending.clear();
}
document.addEventListener("pointerdown", unlock, { capture: true });
document.addEventListener("keydown", unlock, { capture: true });
export const sharedPlayer = player;
export const autoPlayed = new Set<string>();
export function waitForAudioGesture(id: string, retry: () => void) {
  pending.set(id, retry);
}
export function cancelAudioGesture(id: string) {
  pending.delete(id);
}
export function claimAudio(id: string, url: string) {
  if (player.dataset.assetId !== id) {
    player.pause();
    player.dataset.assetId = id;
    player.src = url;
    player.dispatchEvent(new Event("assetchange"));
  }
  return player;
}
