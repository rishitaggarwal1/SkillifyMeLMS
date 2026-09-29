"use client";

import { useEffect, useRef, useState } from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { useQuery } from "@tanstack/react-query";
import { z } from "zod";
import { api } from "@/lib/api/client";
import { unwrap } from "@/lib/api/unwrap";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { uploadVideo, videoFileSchema } from "./upload";

const schema = z.object({ title: z.string().trim().min(1).max(200), file: videoFileSchema });
type Values = z.infer<typeof schema>;

export function VideoUpload({ onReady }: { onReady?: (id: string) => void }) {
  const [videoId, setVideoId] = useState<string>();
  const [progress, setProgress] = useState(0);
  const [error, setError] = useState<string>();
  const controller = useRef<AbortController | null>(null);
  const form = useForm<Values>({ resolver: zodResolver(schema) });
  const video = useQuery({
    queryKey: ["video-upload", videoId],
    enabled: !!videoId,
    queryFn: () =>
      unwrap(api.GET("/api/v1/videos/{video_id}", { params: { path: { video_id: videoId! } } })),
    refetchInterval: (query) =>
      ["ready", "failed"].includes(query.state.data?.status ?? "") ? false : 2000,
  });
  useEffect(() => () => controller.current?.abort(), []);
  useEffect(() => {
    if (video.data?.status === "ready") onReady?.(video.data.id);
  }, [video.data, onReady]);
  const submit = async ({ title, file }: Values) => {
    setError(undefined);
    setProgress(0);
    setVideoId(undefined);
    controller.current = new AbortController();
    try {
      const created = await unwrap(api.POST("/api/v1/videos", { body: { title } }));
      await uploadVideo(file, created.upload, setProgress, controller.current.signal);
      await unwrap(
        api.POST("/api/v1/videos/{video_id}/uploaded", {
          params: { path: { video_id: created.video.id } },
        }),
      );
      setVideoId(created.video.id);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Upload failed.");
    }
  };
  return (
    <form
      onSubmit={(event) => {
        void form.handleSubmit(submit)(event);
      }}
      className="space-y-4"
    >
      <div className="space-y-2">
        <Label htmlFor="video-title">Title</Label>
        <Input id="video-title" {...form.register("title")} />
      </div>
      <div className="space-y-2">
        <Label htmlFor="video-file">MP4 video</Label>
        <Input
          id="video-file"
          type="file"
          accept="video/mp4"
          onChange={(event) => {
            const file = event.target.files?.[0];
            if (file) form.setValue("file", file, { shouldValidate: true });
          }}
        />
      </div>
      {(form.formState.errors.title || form.formState.errors.file) && (
        <p role="alert">
          {form.formState.errors.title?.message ?? form.formState.errors.file?.message}
        </p>
      )}
      <Button disabled={form.formState.isSubmitting} type="submit">
        Upload video
      </Button>
      {form.formState.isSubmitting && (
        <>
          <progress aria-label="Upload progress" max={1} value={progress} />
          <Button type="button" variant="outline" onClick={() => controller.current?.abort()}>
            Cancel
          </Button>
        </>
      )}
      {video.data && (
        <p role="status">
          {video.data.status === "ready"
            ? "Video ready"
            : video.data.status === "failed"
              ? "Video processing failed"
              : "Processing video…"}
        </p>
      )}
      {(error || video.error) && <p role="alert">{error ?? "Could not load processing status."}</p>}
    </form>
  );
}
