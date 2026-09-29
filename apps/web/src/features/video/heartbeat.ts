import type { components } from "@/lib/api/schema";

export type Heartbeat = components["schemas"]["VideoHeartbeat"];
export type WatchIdentity = Pick<Heartbeat, "enrollment_id" | "lesson_id" | "video_asset_id">;

/** Measure continuous playback, never a seek gap. Returns listener cleanup. */
export function trackVideo(
  video: HTMLVideoElement,
  identity: WatchIdentity,
  send: (body: Heartbeat, keepalive: boolean) => void,
) {
  let lastPosition = video.currentTime;
  let lastTime = performance.now();
  let played = 0;
  let lastSentPosition = -1;
  const sample = () => {
    const now = performance.now();
    const delta = video.currentTime - lastPosition;
    const possible = ((now - lastTime) / 1000) * video.playbackRate * 1.5;
    if (!video.seeking && delta > 0 && delta <= possible + 0.25) played += delta;
    lastPosition = video.currentTime;
    lastTime = now;
  };
  const flush = (keepalive = false, position?: number) => {
    // StrictMode may run cleanup before the source has loaded. Never persist that initial zero.
    if (video.readyState === 0) return;
    if (position === undefined) sample();
    const currentPosition = position ?? video.currentTime;
    if (!Number.isFinite(currentPosition) || (played === 0 && lastSentPosition === currentPosition))
      return;
    send(
      {
        ...identity,
        position_seconds: currentPosition,
        played_seconds: Math.min(played, 22.5 * video.playbackRate),
        playback_rate: video.playbackRate,
      },
      keepalive,
    );
    lastSentPosition = currentPosition;
    played = 0;
  };
  const reset = () => {
    played = 0;
    lastPosition = video.currentTime;
    lastTime = performance.now();
  };
  const pause = () => flush();
  const seeking = () => {
    if (played > 0) flush(false, lastPosition);
    reset();
  };
  const hidden = () => {
    if (document.visibilityState === "hidden") flush(true);
  };
  const unload = () => flush(true);
  const timer = window.setInterval(() => {
    if (!video.paused) flush();
  }, 15_000);
  video.addEventListener("timeupdate", sample);
  video.addEventListener("seeking", seeking);
  video.addEventListener("seeked", reset);
  video.addEventListener("play", reset);
  video.addEventListener("pause", pause);
  video.addEventListener("ended", pause);
  document.addEventListener("visibilitychange", hidden);
  window.addEventListener("pagehide", unload);
  return () => {
    flush(true);
    window.clearInterval(timer);
    video.removeEventListener("timeupdate", sample);
    video.removeEventListener("seeking", seeking);
    video.removeEventListener("seeked", reset);
    video.removeEventListener("play", reset);
    video.removeEventListener("pause", pause);
    video.removeEventListener("ended", pause);
    document.removeEventListener("visibilitychange", hidden);
    window.removeEventListener("pagehide", unload);
  };
}
