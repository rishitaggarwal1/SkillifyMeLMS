import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { StrictMode } from "react";
import { VideoPlayer } from "./video-player";

const hls = vi.hoisted(() => ({
  loadSource: vi.fn(),
  attachMedia: vi.fn(),
  destroy: vi.fn(),
  on: vi.fn(),
}));
vi.mock("hls.js/light", () => ({
  default: class {
    static isSupported() {
      return true;
    }
    static Events = { ERROR: "error" };
    loadSource = hls.loadSource;
    attachMedia = hls.attachMedia;
    destroy = hls.destroy;
    on = hls.on;
  },
}));
const playback = {
  url: "https://media.test/video.mp4?signed=yes",
  kind: "mp4" as const,
  expires_at: "2099-01-01T00:00:00Z",
};
afterEach(() => {
  vi.clearAllMocks();
  vi.useRealTimers();
});

it("plays native MP4 and resumes after metadata loads", () => {
  vi.spyOn(HTMLMediaElement.prototype, "load").mockImplementation(() => {});
  const { unmount } = render(
    <StrictMode>
      <VideoPlayer playback={playback} position={30} />
    </StrictMode>,
  );
  const video = screen.getByLabelText("Lesson video") as HTMLVideoElement;
  Object.defineProperty(video, "duration", { value: 120 });
  fireEvent.loadedMetadata(video);
  expect(video.currentTime).toBe(30);
  expect(video.src).toBe(playback.url);
  expect(hls.loadSource).not.toHaveBeenCalled();
  unmount();
});

it("loads hls.js for browsers without native HLS and destroys it on unmount", async () => {
  vi.spyOn(HTMLMediaElement.prototype, "load").mockImplementation(() => {});
  vi.spyOn(HTMLMediaElement.prototype, "canPlayType").mockReturnValue("");
  const { unmount } = render(<VideoPlayer playback={{ ...playback, kind: "hls" }} />);
  await waitFor(() => expect(hls.loadSource).toHaveBeenCalledWith(playback.url));
  expect(hls.attachMedia).toHaveBeenCalled();
  unmount();
  expect(hls.destroy).toHaveBeenCalled();
});

it("requests a fresh URL before expiry", () => {
  vi.useFakeTimers();
  vi.spyOn(HTMLMediaElement.prototype, "load").mockImplementation(() => {});
  const renew = vi.fn();
  const { unmount } = render(
    <VideoPlayer
      playback={{ ...playback, expires_at: new Date(Date.now() + 60_000).toISOString() }}
      onExpired={renew}
    />,
  );
  act(() => vi.advanceTimersByTime(30_000));
  expect(renew).toHaveBeenCalledOnce();
  unmount();
});
