`video.mp4` is an original 12-second blue test video, 160×90 pixels at 10 fps, with no audio.
It contains no third-party media. Generated with FFmpeg:

```sh
ffmpeg -f lavfi -i color=c=blue:s=160x90:r=10 -t 12 -c:v libx264 -pix_fmt yuv420p -movflags +faststart video.mp4
```
