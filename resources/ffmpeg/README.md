# FFmpeg Runtime

Place a Windows FFmpeg executable at:

```text
resources/ffmpeg/ffmpeg.exe
```

The application uses this bundled path first, then falls back to FFmpeg on
`PATH` or the optional `imageio-ffmpeg` package. The binary is intentionally
excluded from Git because GitHub rejects files larger than 100 MB.
