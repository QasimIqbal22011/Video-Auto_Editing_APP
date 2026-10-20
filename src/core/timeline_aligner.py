import os
import sys
import re
import shutil
import subprocess
import tempfile
import threading
from typing import List, Dict, Any, Optional, Callable, Tuple
from .ffmpeg_utils import get_ffmpeg_path, get_audio_info
from .logger import log_info, log_error

def format_seconds_to_time(sec: float) -> str:
    """Formats float seconds into MM:SS.ss or HH:MM:SS."""
    if sec < 0:
        sec = 0.0
    hrs = int(sec // 3600)
    mins = int((sec % 3600) // 60)
    secs = sec % 60
    if hrs > 0:
        return f"{hrs:02d}:{mins:02d}:{secs:05.2f}"
    return f"{mins:02d}:{secs:05.2f}"

def parse_timestamp_from_filename(filename: str, three_part_as_hms: bool = False) -> Optional[float]:
    """
    Extracts timestamp cues in seconds from image filenames.
    Supports formats like:
      - image-1_00:00.png, image-2_00:05.jpg, image_1_00:00.png
      - 00:00.png, 01:23.jpg, 01:05:20.png
      - 00_00.png, 01_23.jpg, 01_05_20.png
      - 00-00.png, 01-23.jpg
      - 00.15.png, 01.23.jpg
      - 0015.png, 0130.jpg (4 digits MMSS)
      - scene_01_23.png, 00_15_intro.jpg
      - 00_15_500.png (with milliseconds)
    """
    stem = os.path.splitext(filename)[0]

    # Pattern 1: HH:MM:SS(.ms) with colons (most specific and standard)
    m_hms_colon = re.search(r'(?:^|[^\d])(\d{1,2}):(\d{2}):(\d{2})(?:[.:](\d{1,3}))?(?:[^\d]|$)', stem)
    if m_hms_colon:
        h = int(m_hms_colon.group(1))
        m = int(m_hms_colon.group(2))
        s = int(m_hms_colon.group(3))
        ms = float("0." + m_hms_colon.group(4)) if m_hms_colon.group(4) else 0.0
        return h * 3600.0 + m * 60.0 + s + ms

    # Pattern 2: MM:SS(.ms) with colon (e.g. 'image-1_00:00', '00:05', '01:23.500')
    m_ms_colon = re.search(r'(?:^|[^\d])(\d{1,3}):(\d{2})(?:[.:](\d{1,3}))?(?:[^\d]|$)', stem)
    if m_ms_colon:
        m = int(m_ms_colon.group(1))
        s = int(m_ms_colon.group(2))
        ms = float("0." + m_ms_colon.group(3)) if m_ms_colon.group(3) else 0.0
        return m * 60.0 + s + ms

    # Check if an image index prefix exists like 'image-1_', 'img_2_', 'slide-3-'
    has_image_prefix = bool(re.search(r'^(?:image|img|slide|clip|pic|photo)[-_]?\d+[-_]', stem, flags=re.IGNORECASE))
    cleaned = re.sub(r'^(?:image|img|slide|clip|pic|photo)[-_]?\d+[-_]', '', stem, flags=re.IGNORECASE)

    # Pattern 3: 4-segment timestamp: HH_MM_SS_MS / HH-MM-SS-MS (e.g. 01_02_03_25)
    m_4part = re.search(r'(?:^|[^\d])(\d{1,2})[-_](\d{2})[-_](\d{2})[-_.](\d{1,3})(?:[^\d]|$)', cleaned)
    if m_4part:
        h = int(m_4part.group(1))
        m = int(m_4part.group(2))
        s = int(m_4part.group(3))
        ms = float("0." + m_4part.group(4))
        return h * 3600.0 + m * 60.0 + s + ms

    # Pattern 4: 3-segment timestamp with underscores/dashes:
    # E.g. 'image-1_00_03_26' (00m 03s 26cs -> 3.26s) or '00_15_500' (00m 15s 500ms -> 15.5s)
    # If the prefix was an image index prefix (e.g. image-1_00_03_26, image-2_01_03_26),
    # or the first segment is 0/00, or the last segment is 3 digits (e.g. 500ms),
    # this represents MM_SS_CS/MS (Minutes, Seconds, Centiseconds/Milliseconds).
    m_3part = re.search(r'(?:^|[^\d])(\d{1,2})[-_](\d{2})[-_.](\d{2,3})(?:[^\d]|$)', cleaned)
    if m_3part:
        p1 = int(m_3part.group(1))
        p2 = int(m_3part.group(2))
        p3_str = m_3part.group(3)
        if three_part_as_hms and len(p3_str) == 2 and int(p3_str) < 60:
            # In timestamped image sequences, a three-part cue may be
            # HH_MM_SS (e.g. image-2_00_00_04 = 4 seconds). Keep the legacy
            # MM_SS_fraction interpretation available for ambiguous names;
            # align_images selects between the two using the audio duration.
            return p1 * 3600.0 + p2 * 60.0 + int(p3_str)
        frac = float("0." + p3_str)
        if has_image_prefix or p1 == 0 or len(p3_str) == 3:
            return p1 * 60.0 + p2 + frac
        else:
            p3 = int(p3_str)
            return p1 * 3600.0 + p2 * 60.0 + p3

    # Pattern 5: MM_SS or MM-SS or MM.SS (e.g. 00_05 -> 5s, 01_23 -> 83s, 00_00 -> 0s)
    m_ms = re.search(r'(?:^|[^\d])(\d{1,3})[-_.](\d{2})(?:[-_.](\d{1,3}))?(?:[^\d]|$)', cleaned)
    if m_ms:
        m = int(m_ms.group(1))
        s = int(m_ms.group(2))
        ms = float("0." + m_ms.group(3)) if m_ms.group(3) else 0.0
        return m * 60.0 + s + ms

    # Pattern 6: 4 consecutive digits (e.g. 0000 -> 0s, 0130 -> 1m30s = 90s)
    m_4d = re.search(r'(?:^|[^\d])(\d{2})(\d{2})(?:[^\d]|$)', cleaned)
    if m_4d:
        m = int(m_4d.group(1))
        s = int(m_4d.group(2))
        if m < 60 and s < 60:
            return m * 60.0 + s

    return None

class TimelineAligner:
    """
    Intelligent timeline image-audio aligner for YouTube video automation.
    Maps timed still images against voiceover audio, produces synchronized
    timeline telemetry, and renders NVENC-accelerated MP4 drafts.
    """

    def __init__(self):
        self._current_process: Optional[subprocess.Popen] = None
        self._is_cancelled = False
        self._lock = threading.Lock()

    def cancel(self):
        """Cancels an in-flight video rendering process."""
        with self._lock:
            self._is_cancelled = True
            if self._current_process and self._current_process.poll() is None:
                try:
                    self._current_process.terminate()
                except Exception:
                    try:
                        self._current_process.kill()
                    except Exception:
                        pass

    def align_images(
        self,
        image_paths: List[str],
        audio_duration: float
    ) -> List[Dict[str, Any]]:
        """
        Calculates the start time, end time, and duration for every image
        along the audio timeline.
        """
        if not image_paths:
            return []

        parsed_items = []
        for idx, img_path in enumerate(image_paths):
            filename = os.path.basename(img_path)
            ts = parse_timestamp_from_filename(filename)
            try:
                image_stat = os.stat(img_path)
                thumbnail_version = f"{image_stat.st_size:x}-{image_stat.st_mtime_ns:x}"
            except OSError:
                thumbnail_version = ""
            parsed_items.append({
                'path': img_path,
                'filename': filename,
                'parsed_timestamp': ts,
                'thumbnail_version': thumbnail_version,
                'original_index': idx
            })

        # Three numeric groups are ambiguous: e.g. 00_00_04 can mean either
        # 4 seconds (HH_MM_SS) or 0.04 seconds (MM_SS_fraction). Infer the
        # intended convention from the complete sequence and voiceover length.
        timestamped_items = [item for item in parsed_items if item['parsed_timestamp'] is not None]
        hms_items = []
        for item in parsed_items:
            hms_timestamp = parse_timestamp_from_filename(item['filename'], three_part_as_hms=True)
            if hms_timestamp is not None:
                hms_items.append((item, hms_timestamp))
        if len(hms_items) >= max(3, int(len(timestamped_items) * 0.7)) and audio_duration > 0:
            legacy_max = max(item['parsed_timestamp'] for item in timestamped_items)
            hms_max = max(timestamp for _, timestamp in hms_items)
            end_tolerance = max(2.0, audio_duration * 0.02)
            if (
                legacy_max < audio_duration * 0.25
                and hms_max > legacy_max * 3
                and hms_max <= audio_duration + end_tolerance
            ):
                for item, hms_timestamp in hms_items:
                    item['parsed_timestamp'] = hms_timestamp

        # Separate items with explicit timestamps and items without
        with_ts = [it for it in parsed_items if it['parsed_timestamp'] is not None]
        without_ts = [it for it in parsed_items if it['parsed_timestamp'] is None]

        # If all or most have timestamps, sort by parsed_timestamp
        if len(with_ts) > 0:
            with_ts.sort(key=lambda x: x['parsed_timestamp'])
            # Any images without timestamps can be placed at the end or interleaved
            ordered_items = with_ts + without_ts
        else:
            # Fallback: keep original file order
            ordered_items = parsed_items

        total_images = len(ordered_items)
        aligned = []

        # Calculate time positions
        for i, item in enumerate(ordered_items):
            # Determine start time
            if item['parsed_timestamp'] is not None:
                start_sec = max(0.0, float(item['parsed_timestamp']))
            else:
                # Evenly distribute if timestamp is missing
                start_sec = round((i / max(1, total_images)) * audio_duration, 2)

            aligned.append({
                'index': i,
                'path': item['path'],
                'filename': item['filename'],
                'thumbnail_version': item['thumbnail_version'],
                'start_sec': start_sec,
                'end_sec': 0.0,
                'duration': 0.0
            })

        # Ensure first image starts at 0.0 if not already
        if aligned:
            aligned[0]['start_sec'] = 0.0

        # Compute end_sec and duration for each image
        for i in range(total_images):
            start = aligned[i]['start_sec']
            if i < total_images - 1:
                next_start = aligned[i + 1]['start_sec']
                # Ensure each image has at least 0.2s duration
                end = max(start + 0.2, next_start)
                # Adjust next image start if necessary to avoid overlaps
                aligned[i + 1]['start_sec'] = max(aligned[i + 1]['start_sec'], end)
            else:
                # Last image holds until the audio ends
                end = max(start + 0.5, audio_duration)

            dur = round(end - start, 3)
            aligned[i]['start_sec'] = round(start, 3)
            aligned[i]['end_sec'] = round(end, 3)
            aligned[i]['duration'] = dur
            aligned[i]['start_formatted'] = format_seconds_to_time(start)
            aligned[i]['end_formatted'] = format_seconds_to_time(end)
            aligned[i]['duration_formatted'] = f"{dur:.2f}s"

        return aligned

    def render_video(
        self,
        aligned_images: List[Dict[str, Any]],
        audio_path: str,
        output_path: str,
        width: int = 1920,
        height: int = 1080,
        fps: int = 30,
        use_hardware_accel: bool = True,
        zoom_type: str = "none",
        zoom_amount: float = 0.12,
        transition_type: str = "hard_cut",
        transition_duration: float = 0.5,
        progress_callback: Optional[Callable[[float, str], None]] = None
    ) -> Dict[str, Any]:
        """
        Renders a 1080p/4K MP4 video with synchronized audio from aligned still images.
        Uses NVENC hardware acceleration when available.
        """
        self._is_cancelled = False

        if not aligned_images:
            raise ValueError("No images provided for timeline alignment.")
        if not os.path.exists(audio_path):
            raise FileNotFoundError(f"Audio file not found: {audio_path}")
        audio_path = os.path.abspath(audio_path)
        output_path = os.path.abspath(output_path)

        width, height, fps = int(width), int(height), int(fps)
        if width <= 0 or height <= 0 or fps <= 0:
            raise ValueError("Video width, height, and fps must be positive")
        zoom_type = str(zoom_type or "none").lower()
        if zoom_type not in {"none", "zoom_in", "zoom_out"}:
            raise ValueError(f"Unsupported zoom type: {zoom_type}")
        zoom_amount = min(0.30, max(0.0, float(zoom_amount)))
        transition_type = str(transition_type or "hard_cut").lower()
        allowed_transitions = {"hard_cut", "fade", "dissolve", "wipeleft", "slideright", "circleopen", "fadeblack"}
        if transition_type not in allowed_transitions:
            raise ValueError(f"Unsupported transition type: {transition_type}")
        transition_duration = min(2.0, max(0.0, float(transition_duration)))

        audio_info = get_audio_info(audio_path)
        total_audio_dur = audio_info['duration']

        if progress_callback:
            progress_callback(5.0, "Preparing timeline video manifest...")

        render_temp_dir = None
        try:
            ffmpeg_bin = get_ffmpeg_path()
            out_dir = os.path.dirname(os.path.abspath(output_path))
            os.makedirs(out_dir, exist_ok=True)

            # Keep FFmpeg's Windows command line short for large image sets.
            # Place aliases alongside the first source when possible so
            # same-volume hard links avoid copying large source images.
            first_image_dir = os.path.dirname(os.path.abspath(aligned_images[0]['path']))
            try:
                render_temp_dir = tempfile.TemporaryDirectory(prefix="vh_render_", dir=first_image_dir)
            except OSError:
                render_temp_dir = tempfile.TemporaryDirectory(prefix="vh_render_")
            render_work_dir = render_temp_dir.name
            staged_image_names = []
            for index, item in enumerate(aligned_images):
                source_path = os.path.abspath(item['path'])
                extension = os.path.splitext(source_path)[1].lower()
                if extension not in {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}:
                    extension = ".img"
                staged_name = f"img_{index:04d}{extension}"
                staged_path = os.path.join(render_work_dir, staged_name)
                try:
                    os.link(source_path, staged_path)
                except OSError:
                    # This is only needed for cross-volume, network, or
                    # filesystems that do not support hard links.
                    shutil.copyfile(source_path, staged_path)
                staged_image_names.append(staged_name)

            # Use Vulkan only for the image crop/resize/zoom stage. Keep the
            # proven CPU xfade graph for transitions and retry the whole render
            # on CPU if this machine cannot initialize Vulkan.
            use_gpu_zoom = (
                use_hardware_accel
                and "ffmpeg-full" in os.path.normcase(ffmpeg_bin)
                and zoom_type != "none"
                and zoom_amount > 0
            )

            # Feed each still as its own timed stream. This allows effects to be
            # applied independently while retaining the alignment durations.
            cmd = [ffmpeg_bin, "-y", "-hide_banner"]
            if use_gpu_zoom:
                cmd.extend(["-init_hw_device", "vulkan=vk", "-filter_hw_device", "vk"])
            durations = []
            for item, staged_name in zip(aligned_images, staged_image_names):
                duration = max(0.04, float(item['duration']))
                durations.append(duration)
                # Match the still-image input cadence to zoompan/output FPS.
                # Leaving image2 at its 25 FPS default causes duplicated frames
                # when rendering at 24/30/60 FPS, which appears as camera shake.
                if use_gpu_zoom:
                    cmd.extend(["-framerate", str(fps), "-i", staged_name])
                else:
                    cmd.extend(["-loop", "1", "-framerate", str(fps), "-t", f"{duration:.4f}", "-i", staged_name])
            audio_input_index = len(aligned_images)
            cmd.extend(["-i", audio_path])

            filter_parts = []
            video_labels = []
            for idx, duration in enumerate(durations):
                source = f"[{idx}:v]"
                label = f"v{idx}"
                # Crop to the target canvas first, then zoom into that canvas.
                base = (
                    f"scale={width}:{height}:force_original_aspect_ratio=increase,"
                    f"crop={width}:{height}"
                )
                frame_count = max(1, round(duration * fps))
                repeat = f"loop=loop={frame_count - 1}:size=1,setpts=N/({fps}*TB)"
                if use_gpu_zoom:
                    if zoom_type == "zoom_in":
                        crop_scale = f"(1+{zoom_amount:.6f}*n/{max(1, frame_count - 1)})"
                    else:
                        crop_scale = f"(1+{zoom_amount:.6f}*(1-n/{max(1, frame_count - 1)}))"
                    aspect = width / height
                    effect = (
                        f"{repeat},format=yuv420p,hwupload,"
                        f"libplacebo=w={width}:h={height}:"
                        f"crop_w='min(iw,ih*{aspect:.8f})/{crop_scale}':"
                        f"crop_h='min(ih,iw/{aspect:.8f})/{crop_scale}':format=yuv420p"
                    )
                elif zoom_type == "none" or zoom_amount == 0:
                    effect = f"{base},{repeat},fps={fps},format=yuv420p"
                else:
                    # Use per-frame perspective mapping instead of zoompan.
                    # zoompan rounds its crop window to integer coordinates,
                    # which produces alternating 1px motion on sharp artwork.
                    # perspective evaluates floating-point corners per frame
                    # with cubic interpolation, giving a stable sub-pixel zoom.
                    zoom_frames = max(2, frame_count)
                    if zoom_type == "zoom_in":
                        z = f"(1+{zoom_amount:.6f}*on/{zoom_frames - 1})"
                    else:
                        z = f"(1+{zoom_amount:.6f}*(1-on/{zoom_frames - 1}))"
                    half_w = f"W/(2*{z})"
                    half_h = f"H/(2*{z})"
                    left, right = f"W/2-{half_w}", f"W/2+{half_w}"
                    top, bottom = f"H/2-{half_h}", f"H/2+{half_h}"
                    effect = (
                        f"{base},{repeat},fps={fps},"
                        f"perspective=eval=frame:interpolation=cubic:"
                        f"x0='{left}':y0='{top}':x1='{right}':y1='{top}':"
                        f"x2='{left}':y2='{bottom}':x3='{right}':y3='{bottom}',"
                        f"format=yuv420p"
                    )
                if use_gpu_zoom:
                    filter_parts.append(f"{source}{effect},trim=duration={duration:.4f},setpts=PTS-STARTPTS[{label}]")
                else:
                    filter_parts.append(f"{source}{effect},trim=duration={duration:.4f},setpts=PTS-STARTPTS,settb=AVTB[{label}]")
                video_labels.append(label)

            if transition_type == "hard_cut" or len(video_labels) == 1:
                if use_gpu_zoom:
                    for label in video_labels:
                        filter_parts.append(f"[{label}]hwdownload,format=yuv420p[dl{label}]")
                    video_labels = [f"dl{label}" for label in video_labels]
                concat_inputs = "".join(f"[{label}]" for label in video_labels)
                filter_parts.append(f"{concat_inputs}concat=n={len(video_labels)}:v=1:a=0[vout]")
            else:
                effective_transition = min(transition_duration, min(durations) / 2.0)
                if effective_transition <= 0:
                    if use_gpu_zoom:
                        for label in video_labels:
                            filter_parts.append(f"[{label}]hwdownload,format=yuv420p[dl{label}]")
                        video_labels = [f"dl{label}" for label in video_labels]
                    concat_inputs = "".join(f"[{label}]" for label in video_labels)
                    filter_parts.append(f"{concat_inputs}concat=n={len(video_labels)}:v=1:a=0[vout]")
                else:
                    if use_gpu_zoom:
                        for label in video_labels:
                            filter_parts.append(f"[{label}]hwdownload,format=yuv420p[dl{label}]")
                        video_labels = [f"dl{label}" for label in video_labels]
                    # xfade consumes part of its first input during the
                    # transition. Hold each non-final clip's last frame for
                    # that duration so the transition starts at the real
                    # timestamp instead of moving the next image earlier.
                    padded_labels = []
                    for idx, label in enumerate(video_labels):
                        if idx < len(video_labels) - 1:
                            padded = f"pad{label}"
                            filter_parts.append(
                                f"[{label}]format=yuv420p,tpad=stop_mode=clone:"
                                f"stop_duration={effective_transition:.4f}[{padded}]"
                            )
                            padded_labels.append(padded)
                        else:
                            padded_labels.append(label)
                    video_labels = padded_labels
                    current = video_labels[0]
                    elapsed = durations[0]
                    for idx in range(1, len(video_labels)):
                        output = f"xf{idx}"
                        # Keep the transition boundary at the actual image
                        # timestamp. Subtracting the transition duration here
                        # makes every following image appear early.
                        offset = elapsed
                        filter_parts.append(
                            f"[{current}][{video_labels[idx]}]xfade=transition={transition_type}"
                            f":duration={effective_transition:.4f}:offset={offset:.4f}[{output}]"
                        )
                        current = output
                        elapsed += durations[idx]
                    # xfade overlaps adjacent clips, so restore the lost overlap
                    # by holding the final frame. This keeps the rendered video
                    # exactly as long as the synchronized voiceover.
                    transition_loss = 0.0
                    pad_duration = max(0.0, total_audio_dur - sum(durations))
                    filter_parts.append(
                        f"[{current}]format=yuv420p,tpad=stop_mode=clone:stop_duration={pad_duration:.4f},"
                        f"trim=duration={total_audio_dur:.4f},setpts=PTS-STARTPTS[vout]"
                    )

            filter_script_path = os.path.join(render_work_dir, "render_filtergraph.txt")
            with open(filter_script_path, "w", encoding="utf-8", newline="\n") as filter_script:
                filter_script.write(";".join(filter_parts))
            # FFmpeg 9 removed -filter_complex_script; the current bundled
            # build reads option values from files with the `-/option` form.
            cmd.extend(["-/filter_complex", filter_script_path, "-map", "[vout]", "-map", f"{audio_input_index}:a"])

            # Choose video encoder: test NVENC if requested
            if use_hardware_accel:
                cmd.extend(["-c:v", "h264_nvenc", "-preset", "p4", "-cq", "22", "-b:v", "8M"])
            else:
                cmd.extend(["-c:v", "libx264", "-preset", "veryfast", "-crf", "20"])

            cmd.extend([
                "-c:a", "aac", "-b:a", "192k",
                "-t", f"{total_audio_dur:.4f}",
                "-shortest",
                output_path
            ])

            log_info(
                f"[TimelineAligner] Executing FFmpeg video render for {len(aligned_images)} images; "
                f"filtergraph stored in script ({os.path.getsize(filter_script_path)} bytes)."
            )

            startupinfo = None
            if sys.platform == "win32":
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                startupinfo.wShowWindow = 0

            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding='utf-8',
                errors='replace',
                startupinfo=startupinfo,
                bufsize=1,
                cwd=render_work_dir
            )

            with self._lock:
                self._current_process = proc

            time_regex = re.compile(r"time=(\d+):(\d+):(\d+\.?\d*)")
            ffmpeg_stderr_tail = []

            for line in proc.stderr:
                ffmpeg_stderr_tail.append(line.rstrip())
                if len(ffmpeg_stderr_tail) > 40:
                    ffmpeg_stderr_tail.pop(0)
                if self._is_cancelled:
                    proc.terminate()
                    raise InterruptedError("Video rendering cancelled by user.")

                t_match = time_regex.search(line)
                if t_match and progress_callback:
                    hours = int(t_match.group(1))
                    mins = int(t_match.group(2))
                    secs = float(t_match.group(3))
                    cur_time = hours * 3600 + mins * 60 + secs
                    pct = 10.0 + min(88.0, (cur_time / max(1.0, total_audio_dur)) * 88.0)
                    progress_callback(
                        round(pct, 1),
                        f"Rendering timeline video ({cur_time:.1f}s / {total_audio_dur:.1f}s)..."
                    )

            proc.wait()

            # If NVENC failed (e.g. driver mismatch), retry with software libx264
            if proc.returncode != 0 and use_hardware_accel and not self._is_cancelled:
                log_info("[TimelineAligner] NVENC render exited non-zero, retrying with CPU libx264...")
                return self.render_video(
                    aligned_images=aligned_images,
                    audio_path=audio_path,
                    output_path=output_path,
                    width=width,
                    height=height,
                    fps=fps,
                    use_hardware_accel=False,
                    progress_callback=progress_callback,
                    zoom_type=zoom_type,
                    zoom_amount=zoom_amount,
                    transition_type=transition_type,
                    transition_duration=transition_duration
                )

            if proc.returncode != 0 and not self._is_cancelled:
                details = "\n".join(ffmpeg_stderr_tail[-12:])
                raise RuntimeError(
                    f"FFmpeg video render failed with exit code {proc.returncode}."
                    + (f"\nFFmpeg details:\n{details}" if details else "")
                )

        finally:
            with self._lock:
                self._current_process = None
            if render_temp_dir is not None:
                render_temp_dir.cleanup()

        if self._is_cancelled:
            if os.path.exists(output_path):
                try:
                    os.remove(output_path)
                except Exception:
                    pass
            raise InterruptedError("Video rendering cancelled by user.")

        if progress_callback:
            progress_callback(100.0, "Timeline video generated successfully!")

        file_size = os.path.getsize(output_path)
        size_str = f"{file_size / (1024 * 1024):.2f} MB" if file_size >= 1024 * 1024 else f"{file_size / 1024:.1f} KB"

        actual_duration = total_audio_dur
        try:
            actual_duration = get_audio_info(output_path)['duration']
        except Exception:
            pass

        return {
            'path': output_path,
            'filename': os.path.basename(output_path),
            'size_bytes': file_size,
            'size_formatted': size_str,
            'duration': actual_duration,
            'duration_formatted': format_seconds_to_time(actual_duration),
            'image_count': len(aligned_images),
            'resolution': f"{width}x{height}",
            'fps': fps
        }

    def export_csv_cue_sheet(self, aligned_images: List[Dict[str, Any]], export_path: str):
        """Exports timeline markers as a CSV cue sheet for DaVinci Resolve / Premiere."""
        import csv
        with open(export_path, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["Index", "Filename", "Start Time", "End Time", "Duration (s)", "File Path"])
            for item in aligned_images:
                writer.writerow([
                    item['index'] + 1, item['filename'], item['start_formatted'],
                    item['end_formatted'], f"{item['duration']:.2f}", item['path']
                ])
        return export_path
