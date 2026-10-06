"use client";

import { useEffect, useRef, useState } from "react";
import type Hls from "hls.js";

import type { components } from "@/lib/api/schema";
import { trackVideo, type WatchIdentity } from "./heartbeat";

type Playback = components["schemas"]["PlaybackOut"];

export function VideoPlayer({
  playback,
  position = 0,
  identity,
  onExpired,
  onVideoChanged,
}: {
  playback: Playback;
  position?: number;
  identity?: WatchIdentity;
  onExpired?: () => void;
  /** The lesson's video was replaced in a new release (heartbeat answered 409 video_changed). */
  onVideoChanged?: () => void;
}) {
  const ref = useRef<HTMLVideoElement>(null);
  const resume = useRef(position);
  const continuePlaying = useRef(false);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    const video = ref.current!;
    let disposed = false;
    let metadataLoaded = false;
    let hls: Hls | undefined;
    const metadata = () => {
      metadataLoaded = true;
      video.currentTime = Math.min(resume.current, video.duration || resume.current);
      if (continuePlaying.current) void video.play().catch(() => {});
    };
    video.addEventListener("loadedmetadata", metadata);
    if (playback.kind === "mp4" || video.canPlayType("application/vnd.apple.mpegurl")) {
      video.src = playback.url;
    } else {
      void import("hls.js/light")
        .then(({ default: Hls }) => {
          if (disposed) return;
          if (!Hls.isSupported()) {
            setError("This browser cannot play this video.");
            return;
          }
          hls = new Hls();
          hls.on(Hls.Events.ERROR, (_, data) => {
            if (data.fatal) setError("Video could not load. Please retry.");
          });
          hls.loadSource(playback.url);
          hls.attachMedia(video);
        })
        .catch(() => setError("Video player could not load. Please retry."));
    }
    const timer = window.setTimeout(
      () => {
        resume.current = video.currentTime;
        onExpired?.();
      },
      Math.min(
        2_147_483_647,
        Math.max(1000, Date.parse(playback.expires_at) - Date.now() - 30_000),
      ),
    );
    return () => {
      disposed = true;
      if (metadataLoaded) {
        resume.current = video.currentTime;
        continuePlaying.current = !video.paused;
      }
      window.clearTimeout(timer);
      video.removeEventListener("loadedmetadata", metadata);
      hls?.destroy();
    };
  }, [playback.url, playback.kind, playback.expires_at, onExpired]);
  // Latest callback without restarting the heartbeat tracker when the parent re-renders.
  const onVideoChangedRef = useRef(onVideoChanged);
  useEffect(() => {
    onVideoChangedRef.current = onVideoChanged;
  }, [onVideoChanged]);
  const enrollmentId = identity?.enrollment_id;
  const lessonId = identity?.lesson_id;
  const assetId = identity?.video_asset_id;
  useEffect(() => {
    if (!enrollmentId || !lessonId || !assetId) return;
    return trackVideo(
      ref.current!,
      { enrollment_id: enrollmentId, lesson_id: lessonId, video_asset_id: assetId },
      (body, keepalive) => {
        void fetch("/backend/api/v1/progress/heartbeat", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
          keepalive,
          credentials: "same-origin",
        })
          .then((response) => {
            if (response.status === 409 && onVideoChangedRef.current) {
              onVideoChangedRef.current(); // a new release replaced the video: load it
            } else if (!response.ok) {
              setError("Progress could not be saved. Reload the lesson to reconnect.");
            }
          })
          .catch(() => setError("Progress could not be saved. Check your connection."));
      },
    );
  }, [enrollmentId, lessonId, assetId]);
  return (
    <div className="space-y-2">
      <video
        ref={ref}
        controls
        playsInline
        preload="metadata"
        className="aspect-video w-full rounded-lg bg-video-surface"
        aria-label="Lesson video"
        onError={() => setError("Video could not load. Please retry.")}
      />
      {error && (
        <p role="alert" className="text-sm text-destructive">
          {error}
        </p>
      )}
    </div>
  );
}
