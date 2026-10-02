import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { trackVideo } from "./heartbeat";

const identity = { enrollment_id: "enrollment", lesson_id: "lesson", video_asset_id: "video" };

describe("video heartbeats", () => {
  beforeEach(() => vi.useFakeTimers({ toFake: ["setInterval", "clearInterval", "performance"] }));
  afterEach(() => vi.useRealTimers());
  const player = () => {
    const video = document.createElement("video");
    Object.defineProperty(video, "paused", { value: false, configurable: true });
    Object.defineProperty(video, "readyState", { value: 4, configurable: true });
    return video;
  };
  it("sends continuous watch time every 15 seconds and removes its timer", () => {
    const video = player();
    const send = vi.fn();
    const cleanup = trackVideo(video, identity, send);
    video.currentTime = 15;
    vi.advanceTimersByTime(15_000);
    expect(send).toHaveBeenLastCalledWith(
      { ...identity, position_seconds: 15, played_seconds: 15, playback_rate: 1 },
      false,
    );
    cleanup();
    const count = send.mock.calls.length;
    vi.advanceTimersByTime(30_000);
    expect(send).toHaveBeenCalledTimes(count);
  });
  it("does not count seeks, and sends pause, visibility and pagehide positions", () => {
    const video = player();
    const send = vi.fn();
    const cleanup = trackVideo(video, identity, send);
    video.currentTime = 90;
    video.dispatchEvent(new Event("seeking"));
    video.dispatchEvent(new Event("seeked"));
    video.dispatchEvent(new Event("pause"));
    expect(send.mock.lastCall?.[0].played_seconds).toBe(0);
    vi.advanceTimersByTime(5000);
    video.currentTime = 95;
    Object.defineProperty(document, "visibilityState", { value: "hidden", configurable: true });
    document.dispatchEvent(new Event("visibilitychange"));
    expect(send.mock.lastCall?.[1]).toBe(true);
    expect(send.mock.lastCall?.[0].played_seconds).toBe(5);
    vi.advanceTimersByTime(1000);
    video.currentTime = 96;
    window.dispatchEvent(new Event("pagehide"));
    expect(send.mock.lastCall?.[0].position_seconds).toBe(96);
    cleanup();
    Object.defineProperty(document, "visibilityState", { value: "visible", configurable: true });
  });
  it("caps delayed heartbeats at the playback-rate-adjusted limit", () => {
    const video = player();
    video.playbackRate = 2;
    const send = vi.fn();
    const cleanup = trackVideo(video, identity, send);
    video.currentTime = 30;
    vi.advanceTimersByTime(15000);
    expect(send.mock.lastCall?.[0].played_seconds).toBe(30);
    cleanup();
  });
  it("keeps the seconds played before a late play handler runs (busy main thread)", () => {
    const video = player();
    video.playbackRate = 2;
    const send = vi.fn();
    const cleanup = trackVideo(video, identity, send);
    const startedAt = performance.now();
    vi.advanceTimersByTime(3000); // the handler runs 3 s after playback actually started...
    video.currentTime = 6; // ...by which time 6 s of video (at 2x) have played
    const play = new Event("play");
    Object.defineProperty(play, "timeStamp", { value: startedAt });
    video.dispatchEvent(play);
    video.dispatchEvent(new Event("pause"));
    expect(send.mock.lastCall?.[0]).toMatchObject({ position_seconds: 6, played_seconds: 6 });
    cleanup();
  });
  it("still ignores an implausible event time", () => {
    const video = player();
    const send = vi.fn();
    const cleanup = trackVideo(video, identity, send);
    vi.advanceTimersByTime(2000);
    video.currentTime = 30; // far more than 2 s of playback can explain
    const play = new Event("play");
    Object.defineProperty(play, "timeStamp", { value: Date.now() }); // epoch, not perf time
    video.dispatchEvent(play);
    video.dispatchEvent(new Event("pause"));
    expect(send.mock.lastCall?.[0].played_seconds ?? 0).toBe(0);
    cleanup();
  });
  it("does not overwrite resume during cleanup before metadata loads", () => {
    const video = document.createElement("video");
    const send = vi.fn();
    const cleanup = trackVideo(video, identity, send);
    cleanup();
    expect(send).not.toHaveBeenCalled();
  });
});
