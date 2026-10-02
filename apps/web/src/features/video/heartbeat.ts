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
  /** When an event happened, in performance.now() time. A busy main thread (low-end phones)
   * can run handlers seconds late; event.timeStamp says when the event actually fired. It is
   * trusted only if plausible (some environments report epoch time instead). */
  const firedAt = (event?: Event) => {
    const now = performance.now();
    const at = event?.timeStamp;
    return at !== undefined && at <= now && now - at < 60_000 ? at : now;
  };
  const reset = (event?: Event) => {
    played = 0;
    lastPosition = video.currentTime;
    lastTime = firedAt(event);
  };
  // Playback starts from where the video was paused (lastPosition: seeks while paused already
  // reset it). Keep that position and take the time playback started, so a late handler doesn't
  // throw away the seconds played before it ran.
  const play = (event: Event) => {
    lastTime = firedAt(event);
  };
  const pause = () => flush();
  const seeking = () => {
    if (played > 0) flush(false, lastPosition);
    reset();
  };
  const seeked = (event: Event) => reset(event);
  const hidden = () => {
    if (document.visibilityState === "hidden") flush(true);
  };
  const unload = () => flush(true);
  const timer = window.setInterval(() => {
    if (!video.paused) flush();
  }, 15_000);
  video.addEventListener("timeupdate", sample);
  video.addEventListener("seeking", seeking);
  video.addEventListener("seeked", seeked);
  video.addEventListener("play", play);
  video.addEventListener("pause", pause);
  video.addEventListener("ended", pause);
  document.addEventListener("visibilitychange", hidden);
  window.addEventListener("pagehide", unload);
  return () => {
    flush(true);
    window.clearInterval(timer);
    video.removeEventListener("timeupdate", sample);
    video.removeEventListener("seeking", seeking);
    video.removeEventListener("seeked", seeked);
    video.removeEventListener("play", play);
    video.removeEventListener("pause", pause);
    video.removeEventListener("ended", pause);
    document.removeEventListener("visibilitychange", hidden);
    window.removeEventListener("pagehide", unload);
  };
}
