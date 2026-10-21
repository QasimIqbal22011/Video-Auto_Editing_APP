import os
import sys
import re
import subprocess
import threading
import tempfile
from collections import OrderedDict
from typing import List, Dict, Any, Optional, Callable, Tuple
from .ffmpeg_utils import get_ffmpeg_path, get_audio_info, get_video_info
from .logger import log_info, log_error

class AudioSilenceRemover:
    """
    Intelligent audio silence and dead-air remover designed for YouTube voiceovers.
    Uses FFmpeg silencedetect to identify unvoiced pauses, merges with safety margin buffers,
    and stitches active speech segments with zero click/pop artifacts.
    """

    def __init__(self):
        self._current_process: Optional[subprocess.Popen] = None
        self._is_cancelled = False
        self._lock = threading.Lock()
        self._analysis_cache_lock = threading.Lock()
        self._analysis_cache: OrderedDict[tuple[str, int, int, float, float], tuple[list[Dict[str, Any]], float]] = OrderedDict()
        self._analysis_cache_limit = 8

    def cancel(self):
        """Cancels an ongoing silence detection or removal job."""
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

    def detect_silence_intervals(
        self,
        file_path: str,
        threshold_db: float = -40.0,
        min_duration_sec: float = 0.4,
        progress_callback: Optional[Callable[[float, str], None]] = None
    ) -> Tuple[List[Dict[str, float]], float]:
        """
        Runs FFmpeg silencedetect on the audio file and returns:
        (list of detected silence intervals [{'start': s, 'end': e, 'duration': d}], total_duration)
        """
        self._is_cancelled = False
        threshold_db = float(threshold_db)
        min_duration_sec = max(0.01, float(min_duration_sec))
        resolved_path = os.path.realpath(file_path)
        file_stat = os.stat(resolved_path)
        cache_key = (
            os.path.normcase(resolved_path),
            file_stat.st_size,
            file_stat.st_mtime_ns,
            threshold_db,
            min_duration_sec,
        )
        with self._analysis_cache_lock:
            cached = self._analysis_cache.get(cache_key)
            if cached is not None:
                self._analysis_cache.move_to_end(cache_key)
                cached_intervals, cached_duration = cached
                intervals_copy = [dict(interval) for interval in cached_intervals]
            else:
                intervals_copy = None
                cached_duration = 0.0
        if intervals_copy is not None:
            if progress_callback:
                progress_callback(10.0, "Using cached silence analysis...")
                progress_callback(45.0, f"Reused silence analysis for {cached_duration:.1f}s of audio.")
            return intervals_copy, cached_duration

        info = get_audio_info(file_path)
        total_duration = info['duration']

        if total_duration <= 0:
            return [], 0.0

        if progress_callback:
            progress_callback(10.0, "Analyzing audio waveform for silences...")

        ffmpeg_bin = get_ffmpeg_path()
        # silencedetect filter
        filter_str = f"silencedetect=noise={threshold_db:.1f}dB:d={min_duration_sec:.2f}"
        cmd = [
            ffmpeg_bin, "-hide_banner", "-i", file_path,
            "-af", filter_str,
            "-f", "null", "-"
        ]

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
            bufsize=1
        )

        with self._lock:
            self._current_process = proc

        silence_start_regex = re.compile(r"silence_start:\s*(-?\d+\.?\d*)")
        silence_end_regex = re.compile(r"silence_end:\s*(-?\d+\.?\d*)\s*\|\s*silence_duration:\s*(-?\d+\.?\d*)")
        time_regex = re.compile(r"time=(\d+):(\d+):(\d+\.?\d*)")

        silence_intervals = []
        pending_start = None

        try:
            for line in proc.stderr:
                if self._is_cancelled:
                    proc.terminate()
                    raise InterruptedError("Silence detection cancelled by user.")

                # Monitor scan progress
                t_match = time_regex.search(line)
                if t_match and progress_callback:
                    hours = int(t_match.group(1))
                    mins = int(t_match.group(2))
                    secs = float(t_match.group(3))
                    cur_time = hours * 3600 + mins * 60 + secs
                    pct = 10.0 + min(35.0, (cur_time / max(1.0, total_duration)) * 35.0)
                    progress_callback(round(pct, 1), f"Scanning for dead air ({cur_time:.1f}s / {total_duration:.1f}s)...")

                # Detect start
                start_match = silence_start_regex.search(line)
                if start_match:
                    pending_start = max(0.0, float(start_match.group(1)))

                # Detect end
                end_match = silence_end_regex.search(line)
                if end_match:
                    end_val = min(total_duration, float(end_match.group(1)))
                    dur_val = float(end_match.group(2))
                    start_val = pending_start if pending_start is not None else max(0.0, end_val - dur_val)
                    if end_val > start_val:
                        dur_val = end_val - start_val
                        silence_intervals.append({
                            'start': round(start_val, 3),
                            'end': round(end_val, 3),
                            'duration': round(dur_val, 3),
                            'start_formatted': f"{int(start_val // 60):02d}:{start_val % 60:05.2f}",
                            'end_formatted': f"{int(end_val // 60):02d}:{end_val % 60:05.2f}",
                            'duration_formatted': f"{dur_val:.2f}s"
                        })
                    pending_start = None

            proc.wait()
            if proc.returncode != 0 and not self._is_cancelled:
                raise RuntimeError(f"FFmpeg silence detection failed with exit code {proc.returncode}")
        finally:
            with self._lock:
                self._current_process = None

        # If silence was ongoing at the very end of file
        if pending_start is not None and pending_start < total_duration:
            dur_val = total_duration - pending_start
            silence_intervals.append({
                'start': round(pending_start, 3),
                'end': round(total_duration, 3),
                'duration': round(dur_val, 3),
                'start_formatted': f"{int(pending_start // 60):02d}:{pending_start % 60:05.2f}",
                'end_formatted': f"{int(total_duration // 60):02d}:{total_duration % 60:05.2f}",
                'duration_formatted': f"{dur_val:.2f}s"
            })

        with self._analysis_cache_lock:
            self._analysis_cache[cache_key] = (
                [dict(interval) for interval in silence_intervals],
                total_duration,
            )
            self._analysis_cache.move_to_end(cache_key)
            while len(self._analysis_cache) > self._analysis_cache_limit:
                self._analysis_cache.popitem(last=False)

        return silence_intervals, total_duration

    def compute_speech_intervals(
        self,
        silence_intervals: List[Dict[str, float]],
        total_duration: float,
        margin_ms: float = 80.0
    ) -> List[Tuple[float, float]]:
        """
        Inverts silence intervals into voiced speech intervals, applying a safety margin
        so words/breaths are never abruptly clipped.
        """
        margin_sec = max(0.0, margin_ms / 1000.0)

        if not silence_intervals:
            return [(0.0, total_duration)]

        # Sort silence intervals by start
        sorted_silences = sorted(silence_intervals, key=lambda x: x['start'])

        # Invert to speech segments
        raw_speech = []
        cur_pos = 0.0

        for s in sorted_silences:
            s_start = s['start']
            s_end = s['end']

            if s_start > cur_pos:
                raw_speech.append((cur_pos, s_start))
            cur_pos = max(cur_pos, s_end)

        if cur_pos < total_duration:
            raw_speech.append((cur_pos, total_duration))

        if not raw_speech:
            return []

        # Apply buffer margins and merge overlapping segments
        buffered_speech = []
        for start, end in raw_speech:
            # Expand start earlier and end later by margin
            b_start = max(0.0, start - margin_sec)
            b_end = min(total_duration, end + margin_sec)
            buffered_speech.append((b_start, b_end))

        # Merge any overlaps
        merged = []
        for start, end in buffered_speech:
            if not merged:
                merged.append((start, end))
            else:
                last_start, last_end = merged[-1]
                if start <= last_end:
                    merged[-1] = (last_start, max(last_end, end))
                else:
                    merged.append((start, end))

        # Filter out negligible micro-segments (< 0.05s)
        final_segments = [(round(s, 3), round(e, 3)) for s, e in merged if (e - s) >= 0.05]
        return final_segments

    def compute_retained_intervals(
        self,
        silence_intervals: List[Dict[str, float]],
        total_duration: float,
        margin_ms: float = 80.0,
        frame_rate: Optional[float] = None,
    ) -> List[Tuple[float, float]]:
        """Return the exact speech intervals the renderer will retain.

        Video intervals are expanded outward to whole source-frame boundaries,
        matching the video renderer's frame-safe cut behavior.
        """
        retained = self.compute_speech_intervals(silence_intervals, total_duration, margin_ms)
        if not frame_rate or frame_rate <= 0:
            return retained

        frame_duration = 1.0 / frame_rate
        snapped: List[Tuple[float, float]] = []
        for start, end in retained:
            frame_start = max(0.0, int(start / frame_duration) * frame_duration)
            frame_end = min(total_duration, int(end / frame_duration + 0.999999) * frame_duration)
            if frame_end <= frame_start:
                continue
            if snapped and frame_start <= snapped[-1][1] + 0.0005:
                snapped[-1] = (snapped[-1][0], max(snapped[-1][1], frame_end))
            else:
                snapped.append((frame_start, frame_end))
        return snapped

    def remove_silences(
        self,
        file_path: str,
        output_path: str,
        threshold_db: float = -40.0,
        min_duration_sec: float = 0.4,
        margin_ms: float = 80.0,
        normalize_audio: bool = True,
        sample_rate: int = 48000,
        output_format: str = "mp3",
        bitrate_kbps: int = 320,
        progress_callback: Optional[Callable[[float, str], None]] = None
    ) -> Dict[str, Any]:
        """
        Full pipeline: detects silences, slices active speech with margins,
        concatenates cleanly, applies optional YouTube loudnorm, and returns results.
        """
        self._is_cancelled = False

        threshold_db = float(threshold_db)
        min_duration_sec = max(0.01, float(min_duration_sec))
        margin_ms = max(0.0, float(margin_ms))
        sample_rate = int(sample_rate)
        if not 8000 <= sample_rate <= 192000:
            raise ValueError("sample_rate must be between 8000 and 192000 Hz")
        bitrate_kbps = max(8, int(bitrate_kbps))
        output_format = str(output_format).lower().lstrip('.')
        if output_format not in {"mp3", "wav", "m4a", "aac"}:
            raise ValueError(f"Unsupported output format: {output_format}")

        if not os.path.exists(file_path):
            raise FileNotFoundError(f"Input file not found: {file_path}")

        # 1. Detect silences
        silences, total_dur = self.detect_silence_intervals(
            file_path=file_path,
            threshold_db=threshold_db,
            min_duration_sec=min_duration_sec,
            progress_callback=progress_callback
        )

        if self._is_cancelled:
            raise InterruptedError("Cancelled by user.")

        total_silence_sec = sum(s['duration'] for s in silences)
        total_silence_count = len(silences)

        # 2. Compute speech segments with safety margin
        speech_segments = self.compute_speech_intervals(silences, total_dur, margin_ms)

        if not speech_segments:
            raise ValueError("All audio was detected as silence. Try raising the threshold or decreasing duration.")

        expected_new_dur = sum(end - start for start, end in speech_segments)
        saved_sec = max(0.0, total_dur - expected_new_dur)
        saved_pct = round((saved_sec / max(0.001, total_dur)) * 100, 1)

        if progress_callback:
            progress_callback(
                50.0,
                f"Found {total_silence_count} pauses ({saved_sec:.1f}s dead air). Trimming..."
            )

        # 3. Build FFmpeg filtergraph to extract and concatenate active speech
        ffmpeg_bin = get_ffmpeg_path()
        out_dir = os.path.dirname(os.path.abspath(output_path))
        os.makedirs(out_dir, exist_ok=True)

        cmd = [ffmpeg_bin, "-y", "-hide_banner", "-i", file_path]

        filter_parts = []
        concat_inputs = []

        for idx, (seg_start, seg_end) in enumerate(speech_segments):
            dur = seg_end - seg_start
            label = f"seg{idx}"
            # atrim, asetpts, and resample
            filter_parts.append(
                f"[0:a]atrim=start={seg_start:.3f}:end={seg_end:.3f},asetpts=PTS-STARTPTS,"
                f"aresample={sample_rate},aformat=sample_fmts=fltp:channel_layouts=stereo[{label}]"
            )
            concat_inputs.append(f"[{label}]")

        concat_filter = "".join(concat_inputs) + f"concat=n={len(speech_segments)}:v=0:a=1"

        if normalize_audio:
            concat_filter += ",loudnorm=I=-16:TP=-1.5:LRA=11"

        filter_parts.append(concat_filter + "[outa]")
        full_filtergraph = ";".join(filter_parts)
        # Keep long timelines out of the Windows process command line; a
        # filter script avoids WinError 206 when many pauses produce segments.
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", suffix=".ffscript", prefix="vh_silence_",
            dir=out_dir, delete=False, newline="\n"
        ) as filter_script:
            filter_script.write(full_filtergraph)
            filter_script_path = filter_script.name

        cmd.extend(["-/filter_complex", filter_script_path, "-map", "[outa]"])

        # Output encoding
        out_fmt = output_format.lower().replace('.', '')
        if out_fmt == "mp3":
            cmd.extend(["-c:a", "libmp3lame", "-b:a", f"{bitrate_kbps}k"])
        elif out_fmt == "wav":
            cmd.extend(["-c:a", "pcm_s16le"])
        elif out_fmt in ("m4a", "aac"):
            cmd.extend(["-c:a", "aac", "-b:a", f"{bitrate_kbps}k"])
        else:
            cmd.extend(["-c:a", "libmp3lame", "-b:a", f"{bitrate_kbps}k"])

        cmd.extend(["-ar", str(sample_rate)])
        cmd.append(output_path)

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
            bufsize=1
        )

        with self._lock:
            self._current_process = proc

        time_regex = re.compile(r"time=(\d+):(\d+):(\d+\.?\d*)")
        stderr_tail: List[str] = []

        try:
            for line in proc.stderr:
                stderr_tail.append(line.rstrip())
                if len(stderr_tail) > 40:
                    stderr_tail.pop(0)
                if self._is_cancelled:
                    proc.terminate()
                    raise InterruptedError("Cancelled by user.")

                t_match = time_regex.search(line)
                if t_match and progress_callback:
                    hours = int(t_match.group(1))
                    mins = int(t_match.group(2))
                    secs = float(t_match.group(3))
                    cur_time = hours * 3600 + mins * 60 + secs
                    pct = 50.0 + min(46.0, (cur_time / max(0.1, expected_new_dur)) * 46.0)
                    progress_callback(round(pct, 1), f"Rendering tight master track ({cur_time:.1f}s / {expected_new_dur:.1f}s)...")

            proc.wait()
            if proc.returncode != 0 and not self._is_cancelled:
                raise RuntimeError(
                    f"FFmpeg silence removal failed with exit code {proc.returncode}:\n"
                    + "\n".join(stderr_tail[-12:])
                )

        finally:
            with self._lock:
                self._current_process = None
            try:
                os.remove(filter_script_path)
            except OSError:
                pass

        if self._is_cancelled:
            if os.path.exists(output_path):
                try:
                    os.remove(output_path)
                except Exception:
                    pass
            raise InterruptedError("Silence removal cancelled by user.")

        if progress_callback:
            progress_callback(100.0, "Voiceover tightened and ready!")

        final_info = get_audio_info(output_path)

        return {
            'output_info': final_info,
            'path': final_info['path'],
            'filename': final_info['filename'],
            'duration': final_info['duration'],
            'duration_formatted': final_info['duration_formatted'],
            'size_bytes': final_info['size_bytes'],
            'size_formatted': final_info['size_formatted'],
            'format_name': final_info['format_name'],
            'sample_rate': final_info['sample_rate'],
            'original_duration': total_dur,
            'original_duration_formatted': f"{int(total_dur // 60):02d}:{int(total_dur % 60):02d}",
            'trimmed_duration': final_info['duration'],
            'trimmed_duration_formatted': final_info['duration_formatted'],
            'silence_count': total_silence_count,
            'silence_removed_sec': round(saved_sec, 2),
            'time_saved_sec': round(saved_sec, 2),
            'percent_saved': saved_pct,
            'time_saved_percent': saved_pct,
            'silence_segments': silences[:100],  # First 100 silence intervals for telemetry UI
            'kept_intervals': [[round(start, 6), round(end, 6)] for start, end in speech_segments],
        }

    def remove_silences_from_video(
        self,
        file_path: str,
        output_path: str,
        threshold_db: float = -40.0,
        min_duration_sec: float = 0.4,
        margin_ms: float = 80.0,
        normalize_audio: bool = True,
        use_hardware_accel: bool = True,
        progress_callback: Optional[Callable[[float, str], None]] = None,
    ) -> Dict[str, Any]:
        """Remove detected silent intervals from both streams of a video.

        Audio and video are trimmed by the same kept-time intervals, snapped
        outward to the source video's frame grid so visual cuts occur on full
        frames without sacrificing the speech-safety margin. Returns the kept
        intervals for retiming any original still-image timeline.
        """
        self._is_cancelled = False
        if not os.path.isfile(file_path):
            raise FileNotFoundError(f"Input video not found: {file_path}")
        video_info = get_video_info(file_path)
        total_duration = float(video_info["duration"])
        fps = float(video_info.get("fps") or 30.0)

        silences, detected_duration = self.detect_silence_intervals(
            file_path=file_path,
            threshold_db=threshold_db,
            min_duration_sec=min_duration_sec,
            progress_callback=progress_callback,
        )
        total_duration = min(total_duration, detected_duration) if detected_duration > 0 else total_duration
        if self._is_cancelled:
            raise InterruptedError("Video silence removal cancelled by user.")

        speech_segments = self.compute_retained_intervals(
            silences, total_duration, margin_ms, frame_rate=fps
        )
        if not speech_segments:
            raise ValueError("All video audio was detected as silence. Adjust the silence settings and try again.")

        # Outward snapping preserves speech and gives matching audio/video
        # boundaries on the source video's frame grid.
        kept_intervals = speech_segments
        if not kept_intervals:
            raise ValueError("No frame-aligned speech intervals remain after silence analysis.")

        expected_duration = sum(end - start for start, end in kept_intervals)
        saved_sec = max(0.0, total_duration - expected_duration)
        saved_pct = round(100.0 * saved_sec / max(0.001, total_duration), 1)
        if progress_callback:
            progress_callback(
                50.0,
                f"Found {len(silences)} pauses ({saved_sec:.1f}s). Retiming video and audio together...",
            )

        ffmpeg_bin = get_ffmpeg_path()
        out_dir = os.path.dirname(os.path.abspath(output_path))
        os.makedirs(out_dir, exist_ok=True)
        audio_output_path = os.path.splitext(output_path)[0] + "_audio.m4a"
        temp_dir = tempfile.TemporaryDirectory(prefix="vh_silence_video_", dir=out_dir)
        staged_video = os.path.join(temp_dir.name, "synchronized.mp4")
        staged_audio = os.path.join(temp_dir.name, "synchronized_audio.m4a")
        filter_script = os.path.join(temp_dir.name, "trim_timeline.ffscript")

        graph_parts = []
        concat_labels = []
        video_inputs = "".join(f"[vsrc{index}]" for index in range(len(kept_intervals)))
        audio_inputs = "".join(f"[asrc{index}]" for index in range(len(kept_intervals)))
        graph_parts.append(f"[0:v:0]split={len(kept_intervals)}{video_inputs}")
        graph_parts.append(f"[0:a:0]asplit={len(kept_intervals)}{audio_inputs}")
        for index, (start, end) in enumerate(kept_intervals):
            graph_parts.append(
                f"[vsrc{index}]trim=start={start:.6f}:end={end:.6f},setpts=PTS-STARTPTS[v{index}]"
            )
            graph_parts.append(
                f"[asrc{index}]atrim=start={start:.6f}:end={end:.6f},"
                f"asetpts=PTS-STARTPTS,aresample=48000,"
                f"aformat=sample_fmts=fltp:channel_layouts=stereo[a{index}]"
            )
            concat_labels.append(f"[v{index}][a{index}]")
        graph_parts.append(
            "".join(concat_labels)
            + f"concat=n={len(kept_intervals)}:v=1:a=1[vcat][acat]"
        )
        if normalize_audio:
            graph_parts.append("[acat]loudnorm=I=-16:TP=-1.5:LRA=11[aout]")
        else:
            graph_parts.append("[acat]anull[aout]")
        with open(filter_script, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(";\n".join(graph_parts))

        startupinfo = None
        if sys.platform == "win32":
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = 0

        def run_render(encoder_args: List[str]) -> List[str]:
            cmd = [
                ffmpeg_bin, "-y", "-hide_banner", "-i", file_path,
                "-/filter_complex", filter_script,
                "-map", "[vcat]", "-map", "[aout]",
                "-r", f"{fps:.6f}", "-fps_mode", "cfr",
                *encoder_args,
                "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart",
                staged_video,
            ]
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                startupinfo=startupinfo,
                bufsize=1,
            )
            with self._lock:
                self._current_process = proc
            stderr_tail: List[str] = []
            time_regex = re.compile(r"time=(\d+):(\d+):(\d+\.?\d*)")
            try:
                if proc.stderr is not None:
                    for line in proc.stderr:
                        stderr_tail.append(line.rstrip())
                        if len(stderr_tail) > 40:
                            stderr_tail.pop(0)
                        if self._is_cancelled:
                            proc.terminate()
                            raise InterruptedError("Video silence removal cancelled by user.")
                        match = time_regex.search(line)
                        if match and progress_callback:
                            current = (
                                int(match.group(1)) * 3600
                                + int(match.group(2)) * 60
                                + float(match.group(3))
                            )
                            progress_callback(
                                50.0 + min(45.0, 45.0 * current / max(0.1, expected_duration)),
                                f"Rendering synchronized video ({current:.1f}s / {expected_duration:.1f}s)...",
                            )
                proc.wait()
            finally:
                with self._lock:
                    self._current_process = None
            if proc.returncode != 0:
                raise RuntimeError(
                    f"FFmpeg synchronized video render failed ({proc.returncode}):\n"
                    + "\n".join(stderr_tail[-12:])
                )
            return stderr_tail

        try:
            try:
                if use_hardware_accel:
                    run_render(["-c:v", "h264_nvenc", "-preset", "p4", "-cq", "22", "-b:v", "8M"])
                else:
                    run_render(["-c:v", "libx264", "-preset", "veryfast", "-crf", "20"])
            except InterruptedError:
                raise
            except Exception as hardware_error:
                if not use_hardware_accel:
                    raise
                log_error(f"[SilenceRemover] NVENC video trim failed; retrying with libx264: {hardware_error}")
                if os.path.exists(staged_video):
                    os.remove(staged_video)
                run_render(["-c:v", "libx264", "-preset", "veryfast", "-crf", "20"])

            # A compact AAC sidecar lets Zoom & Transitions consume the
            # silence-trimmed voiceover without extracting/re-uploading it.
            extract = subprocess.run(
                [
                    ffmpeg_bin, "-y", "-hide_banner", "-i", staged_video,
                    "-map", "0:a:0", "-vn", "-c:a", "copy", staged_audio,
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                startupinfo=startupinfo,
                check=False,
            )
            if extract.returncode != 0:
                raise RuntimeError(f"Could not extract trimmed audio for the Zoom handoff: {extract.stderr[-2000:]}")
            if self._is_cancelled:
                raise InterruptedError("Video silence removal cancelled by user.")

            os.replace(staged_audio, audio_output_path)
            os.replace(staged_video, output_path)
        finally:
            if self._is_cancelled:
                for partial in (staged_video, staged_audio, output_path, audio_output_path):
                    if os.path.isfile(partial):
                        try:
                            os.remove(partial)
                        except OSError:
                            pass
            temp_dir.cleanup()

        final_video = get_video_info(output_path)
        final_audio = get_audio_info(audio_output_path)
        actual_saved_sec = max(0.0, total_duration - float(final_video["duration"]))
        actual_saved_pct = round(100.0 * actual_saved_sec / max(0.001, total_duration), 1)
        return {
            "media_type": "video",
            "path": output_path,
            "filename": os.path.basename(output_path),
            "duration": final_video["duration"],
            "duration_formatted": final_video["duration_formatted"],
            "size_bytes": final_video["size_bytes"],
            "size_formatted": final_video["size_formatted"],
            "format_name": "MP4",
            "video_info": final_video,
            "audio_path": audio_output_path,
            "audio_info": final_audio,
            "original_duration": total_duration,
            "trimmed_duration": final_video["duration"],
            "silence_count": len(silences),
            "silence_removed_sec": round(actual_saved_sec, 2),
            "percent_saved": actual_saved_pct,
            "silence_segments": silences[:100],
            "kept_intervals": [[round(start, 6), round(end, 6)] for start, end in kept_intervals],
        }
