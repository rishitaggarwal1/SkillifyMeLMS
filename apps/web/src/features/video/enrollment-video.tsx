"use client";

import { useCallback } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api/client";
import { unwrap } from "@/lib/api/unwrap";
import { VideoPlayer } from "./video-player";

export function EnrollmentVideo({
  enrollmentId,
  lessonId,
  onVideoChanged,
}: {
  enrollmentId: string;
  lessonId: string;
  onVideoChanged?: () => void;
}) {
  const path = { enrollment_id: enrollmentId, lesson_id: lessonId };
  const playback = useQuery({
    queryKey: ["video-playback", enrollmentId, lessonId],
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/enrollments/{enrollment_id}/lessons/{lesson_id}/playback", {
          params: { path },
        }),
      ),
    refetchOnWindowFocus: false,
    staleTime: Infinity,
  });
  const resume = useQuery({
    queryKey: ["video-resume", enrollmentId, lessonId],
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/enrollments/{enrollment_id}/lessons/{lesson_id}/resume", {
          params: { path },
        }),
      ),
    refetchOnWindowFocus: false,
    staleTime: 0,
  });
  const { refetch } = playback;
  const { refetch: refetchResume } = resume;
  const renew = useCallback(() => {
    void refetch();
  }, [refetch]);
  // A new release replaced this lesson's video: fetch its playback and (reset) resume point; the
  // player remounts on the new asset id.
  const reload = useCallback(() => {
    void refetchResume();
    void refetch();
    onVideoChanged?.();
  }, [refetch, refetchResume, onVideoChanged]);
  if (playback.error || resume.error)
    return <p role="alert">This video is unavailable. Check your course access and try again.</p>;
  if (!playback.data || !resume.data) return <p role="status">Loading video…</p>;
  return (
    <VideoPlayer
      key={`${enrollmentId}:${lessonId}:${resume.data.video_asset_id}`}
      playback={playback.data}
      position={resume.data.position_seconds}
      identity={{ ...path, video_asset_id: resume.data.video_asset_id }}
      onExpired={renew}
      onVideoChanged={reload}
    />
  );
}
