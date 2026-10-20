# Video Helper Studio (YouTube Automation Desktop App)

A modern, standalone desktop application designed to automate tedious editing workflows for YouTube Automation content creators. Built with Python, pywebview (Chromium WebView2), and high-performance FFmpeg hardware acceleration.

> 📘 **For AI Assistants & Developers**: See [AI_DEVELOPER_GUIDE.md](file:///a:/My%20Video%20Hwelper%20APP/AI_DEVELOPER_GUIDE.md) for the complete engineering manual, internal contracts, threading rules, and gotchas.

---

## Active Workflow Engines

### 🎚️ 1. Audio Stitcher & Master Voiceover Builder (Ready)
- **Multi-Format Input**: Support for `.mp3`, `.wav`, `.m4a`, `.aac`, `.flac`, `.ogg`, and `.wma`.
- **Drag-and-Drop & Reordering**: Easily reorder clips via drag-and-drop, Move Up / Move Down buttons, or **Natural Alphanumeric Sorting** (`1.wav`, `2.wav`, `10.wav`).
- **Inline Clip Preview**: Instant audio preview for each added clip directly in the list.
- **Smart Pause Spacing**: Configurable pause/gap between clips (`0.0s`, `0.2s`, `0.3s`, `0.5s`, `1.0s`, or custom slider).
- **YouTube Loudness Mastering**: Built-in EBU R128 (`-16 LUFS`, `-1.5 dB True Peak`) normalization to prevent volume imbalances between voiceover takes.
- **Pipeline Integration**: 1-click **"Send to Silence Remover"** and **"Send to Timeline"** buttons.

### ✂️ 2. Audio Silence Remover (Ready)
- **Dead-Air Removal**: Uses FFmpeg `silencedetect` with configurable dB threshold and minimum duration.
- **Voice Safety Buffers**: Adds configurable margin padding (`80ms` recommended) to the start and end of speech segments to guarantee word endings and natural breaths are never clipped.
- **Micro-Crossfade Concatenation**: Assembles active speech segments with zero click/pop artifacts.
- **Live Telemetry UI**: Real-time list of detected pauses with timestamps and time saved.
- **Pipeline Integration**: 1-click **"Send to Stitcher"** and **"Send to Timeline"** buttons.

### ⏱️ 3. Smart Timeline Image Aligner (Ready)
- **Automated Mapping**: Parses timestamp cues from filenames (`image-1_00:00.png`, `00:00.png`, `00_07.jpg`, `01_23.png`, `0015.png`, etc.) and positions each image along the audio timeline.
- **Interactive Sequence Cards**: Displays live image thumbnails with start/end timestamps and duration badges.
- **NVENC Hardware Acceleration**: High-speed video rendering using **NVIDIA RTX 3060 NVENC** (`h264_nvenc`) with CPU fallback (`libx264`).
- **Embedded Video Player**: Watch and review the rendered 1080p video directly inside the app.
- **CSV Cue Sheet**: Exports a timestamped CSV sheet for direct import into Premiere Pro / DaVinci Resolve.

### 🎬 4. Zoom & Transitions (Ready)
- Receives an aligned image timeline from the Timeline Aligner through **Send to Zoom & Transitions**.
- Applies slow zoom-in or zoom-out motion to every aligned image.
- Offers hard cuts, fade, dissolve, wipe, slide, circle, and fade-through-black transitions.
- Exports the completed video with the synchronized voiceover through **Apply Effects & Export Video**.

---

## 🎨 Design & Aesthetic
- **Native Desktop Window**: Standalone desktop window powered by pywebview (no browser tabs, no command prompt popup).
- **Dark Obsidian & Crisp Light Themes**: Fast toggle with persisted user preference.
- **Hardware Status Indicator**: Automatically checks and displays GPU NVENC and CPU specs during startup.

---

## 🚀 How to Run

## Clone and Run

The full FFmpeg binaries are stored with Git LFS. Install Git LFS before cloning so the binaries are downloaded correctly.

### 1. Clone the branch

```powershell
git lfs install
git clone --branch fix/frontend-polish-preview https://github.com/QasimIqbal22011/Video-Auto_Editing_APP.git
cd Video-Auto_Editing_APP
git lfs pull
```

### 2. Prerequisites

- Python 3.11+
- Git LFS 3+
- WebView2 Runtime on Windows for the native desktop window

Create the Python environment and install dependencies:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

### 3. Run from source

```powershell
python src/main.py
```

The app prefers `resources\ffmpeg-full\bin\ffmpeg.exe` automatically.

### 4. Run the packaged executable

```text
dist\VideoHelper\VideoHelper.exe
```

### 5. Run tests and rebuild

```powershell
python tests/test_stitcher.py
python tests/test_silence.py
python tests/test_timeline.py
python build_exe.py
```

The rebuilt executable is written to `dist\VideoHelper\VideoHelper.exe`.
