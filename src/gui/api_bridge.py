import os
import sys
import subprocess
import threading
import json
import platform
from concurrent.futures import ThreadPoolExecutor
from typing import List, Dict, Any, Callable, Optional
import webview

from ..core.ffmpeg_utils import get_audio_info, get_ffmpeg_path
from ..core.audio_stitcher import AudioStitcher
from ..core.silence_remover import AudioSilenceRemover
from ..core.timeline_aligner import TimelineAligner, parse_timestamp_from_filename
from ..core.logger import log_info, log_error
from .dialog_utils import pick_files, pick_save
from .media_access import MediaPathRegistry

class ApiBridge:
    """
    Python backend API exposed directly to Javascript via pywebview.
    """
    def __init__(self, media_registry: Optional[MediaPathRegistry] = None):
        self._window: Optional[webview.Window] = None
        self._stitcher = AudioStitcher()
        self._silence_remover = AudioSilenceRemover()
        self._timeline_aligner = TimelineAligner()
        self._media_registry = media_registry or MediaPathRegistry()
        self._job_locks = {
            "stitch": threading.Lock(),
            "silence": threading.Lock(),
            "render": threading.Lock(),
        }

    def _emit_js(self, callback_name: str, *args: Any) -> None:
        """Serialize callback arguments as JSON to preserve arbitrary text safely."""
        if not self._window:
            return
        payload = json.dumps(args, ensure_ascii=False)
        try:
            self._window.evaluate_js(f"window[{json.dumps(callback_name)}](...{payload});")
        except Exception as exc:
            log_error(f"[ApiBridge] Could not deliver {callback_name} callback: {exc}")

    def _start_job(
        self,
        job_name: str,
        target: Callable[[], None],
        busy_callback: str
    ) -> Dict[str, str]:
        """Serialize jobs using each stateful engine and release locks on every exit."""
        job_lock = self._job_locks[job_name]
        if not job_lock.acquire(blocking=False):
            message = f"A {job_name} operation is already running. Wait for it to finish or cancel it first."
            self._emit_js(busy_callback, message)
            return {"status": "busy", "error": message}

        def run_job():
            try:
                target()
            finally:
                job_lock.release()

        try:
            threading.Thread(target=run_job, name=f"VideoHelper-{job_name}", daemon=True).start()
        except Exception:
            job_lock.release()
            raise
        return {"status": "started"}

    def _inspect_audio_file(self, file_path: str) -> Dict[str, Any]:
        extension = os.path.splitext(file_path)[1].lower()
        try:
            info = get_audio_info(file_path)
            self._media_registry.register(file_path)
            return info
        except Exception as exc:
            return {
                'path': file_path,
                'filename': os.path.basename(file_path),
                'duration': 0,
                'duration_formatted': '00:00',
                'sample_rate': 44100,
                'channels': 2,
                'bitrate_kbps': 0,
                'format_name': extension.replace('.', '').upper(),
                'size_bytes': os.path.getsize(file_path) if os.path.isfile(file_path) else 0,
                'size_formatted': '0 KB',
                'error': str(exc)
            }

    def _inspect_audio_files(self, file_paths: List[str]) -> List[Dict[str, Any]]:
        """Probe a small bounded batch in parallel, preserving the user's file order."""
        if len(file_paths) < 2:
            return [self._inspect_audio_file(path) for path in file_paths]
        with ThreadPoolExecutor(max_workers=min(4, len(file_paths)), thread_name_prefix="AudioProbe") as pool:
            return list(pool.map(self._inspect_audio_file, file_paths))

    def set_window(self, window: webview.Window):
        self._window = window

    @staticmethod
    def _resolve_output_path(output_path: str) -> str:
        """Resolve UI-relative export paths against this project, not the CWD."""
        if not output_path or not output_path.strip():
            raise ValueError("Output path is required")
        base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
        return output_path if os.path.isabs(output_path) else os.path.join(base_dir, output_path)

    def select_audio_files(self) -> List[Dict[str, Any]]:
        """
        Opens the native Windows file dialog to pick one or more audio files.
        Returns a list of audio metadata dictionaries.
        """
        try:
            selected = pick_files()
        except Exception as e:
            print(f"[ApiBridge] Error in pick_files: {e}")
            selected = []

        if not selected:
            return []

        return self._inspect_audio_files(selected)

    def inspect_dropped_files(self, file_paths: List[str]) -> List[Dict[str, Any]]:
        """
        Takes raw file paths and returns audio metadata.
        """
        valid_exts = {'.mp3', '.wav', '.m4a', '.aac', '.flac', '.ogg', '.wma'}
        valid_paths = [
            path for path in file_paths
            if os.path.isfile(path) and os.path.splitext(path)[1].lower() in valid_exts
        ]
        return self._inspect_audio_files(valid_paths)

    def select_save_destination(self, default_name: str = "stitched_master.mp3") -> Optional[str]:
        """
        Opens native Windows Save As dialog.
        """
        log_info(f"[ApiBridge] select_save_destination called with default_name={default_name}")
        try:
            res = pick_save(default_name)
            log_info(f"[ApiBridge] pick_save returned: {res}")
            return res
        except Exception as e:
            log_error(f"[ApiBridge] Error in pick_save: {e}")
            return None

    def start_stitch(
        self,
        file_paths: List[str],
        output_path: str,
        gap_sec: float = 0.3,
        normalize_audio: bool = True,
        sample_rate: int = 48000,
        output_format: str = "mp3",
        bitrate_kbps: int = 320
    ):
        """
        Starts the stitching process in a dedicated background worker thread.
        Emits progress and completion events to Javascript via evaluate_js.
        """
        output_path = self._resolve_output_path(output_path)
        log_info(f"[ApiBridge] start_stitch: {len(file_paths)} files, out={output_path}, gap={gap_sec}s, norm={normalize_audio}")
        def run():
            def on_progress(pct: float, msg: str):
                if self._window:
                    self._emit_js("onStitchProgress", pct, msg)

            try:
                result = self._stitcher.stitch(
                    file_paths=file_paths,
                    output_path=output_path,
                    gap_sec=gap_sec,
                    normalize_audio=normalize_audio,
                    sample_rate=sample_rate,
                    output_format=output_format,
                    bitrate_kbps=bitrate_kbps,
                    progress_callback=on_progress
                )
                self._media_registry.register(result.get('path', output_path))
                if self._window:
                    self._emit_js("onStitchComplete", result)
            except InterruptedError:
                if self._window:
                    self._emit_js("onStitchCancelled")
            except Exception as e:
                if self._window:
                    self._emit_js("onStitchError", str(e))

        return self._start_job("stitch", run, "onStitchError")

    def cancel_stitch(self):
        """Cancels an in-flight stitching operation."""
        self._stitcher.cancel()
        return {"status": "cancelling"}

    def open_folder(self, file_path: str):
        """Reveals the file in Windows File Explorer."""
        if not file_path or not os.path.exists(file_path):
            return False
        abs_path = os.path.abspath(file_path)
        subprocess.run(["explorer.exe", f"/select,{abs_path}"], check=False)
        return True

    def get_hardware_info(self) -> Dict[str, Any]:
        """Returns detected hardware and engine specs."""
        ffmpeg_bin = ""
        try:
            ffmpeg_bin = get_ffmpeg_path()
            ffmpeg_status = "Ready (v7.1)"
        except Exception as e:
            ffmpeg_status = f"Missing: {e}"

        nvenc_ready = False
        if ffmpeg_bin:
            try:
                startupinfo = None
                creationflags = 0
                if sys.platform == "win32":
                    startupinfo = subprocess.STARTUPINFO()
                    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                    startupinfo.wShowWindow = 0
                    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
                probe = subprocess.run(
                    [ffmpeg_bin, "-hide_banner", "-encoders"],
                    capture_output=True,
                    text=True,
                    timeout=10,
                    startupinfo=startupinfo,
                    creationflags=creationflags
                )
                nvenc_ready = "h264_nvenc" in probe.stdout
            except (OSError, subprocess.SubprocessError):
                pass
        return {
            "cpu": platform.processor() or platform.machine(),
            "gpu": "NVENC encoder available" if nvenc_ready else "GPU encoder unavailable",
            "nvenc_ready": nvenc_ready,
            "ffmpeg_status": ffmpeg_status,
            "ffmpeg_path": ffmpeg_bin
        }

    def initialize_app(self) -> Dict[str, Any]:
        """
        Performs startup warm-up: verifies FFmpeg, checks GPU NVENC readiness,
        and initializes temporary directories.
        """
        log_info("[ApiBridge] Startup warmup initiated...")
        ffmpeg_path = ""
        try:
            ffmpeg_path = get_ffmpeg_path()
            ffmpeg_ok = True
        except Exception as e:
            log_error("[ApiBridge] FFmpeg check during warmup failed:", e)
            ffmpeg_ok = False

        base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
        import_dir = os.path.join(base_dir, 'imported_audio')
        os.makedirs(import_dir, exist_ok=True)
        img_import_dir = os.path.join(base_dir, 'imported_images')
        os.makedirs(img_import_dir, exist_ok=True)

        log_info(f"[ApiBridge] Startup warmup complete. FFmpeg={ffmpeg_ok}")
        hardware = self.get_hardware_info()
        return {
            "ready": True,
            "ffmpeg": ffmpeg_ok,
            "ffmpeg_path": ffmpeg_path,
            "gpu": hardware["gpu"],
            "cpu": hardware["cpu"],
            "nvenc": hardware["nvenc_ready"]
        }

    def detect_silences(
        self,
        file_path: str,
        threshold_db: float = -40.0,
        min_duration_sec: float = 0.4
    ) -> Dict[str, Any]:
        """
        Analyzes audio file for silences without exporting, returning telemetry.
        """
        log_info(f"[ApiBridge] detect_silences: {file_path}, thresh={threshold_db}dB, min_dur={min_duration_sec}s")
        try:
            silences, total_dur = self._silence_remover.detect_silence_intervals(
                file_path=file_path,
                threshold_db=threshold_db,
                min_duration_sec=min_duration_sec
            )
            total_silence_sec = sum(s['duration'] for s in silences)
            new_dur = max(0.0, total_dur - total_silence_sec)
            pct_saved = round((total_silence_sec / max(0.001, total_dur)) * 100, 1)

            return {
                "success": True,
                "silence_count": len(silences),
                "total_silence_sec": round(total_silence_sec, 2),
                "total_duration": total_dur,
                "total_duration_formatted": f"{int(total_dur // 60):02d}:{int(total_dur % 60):02d}",
                "expected_trimmed_duration": round(new_dur, 2),
                "expected_trimmed_formatted": f"{int(new_dur // 60):02d}:{int(new_dur % 60):02d}",
                "percent_saved": pct_saved,
                "silence_segments": silences[:150]
            }
        except Exception as e:
            log_error("[ApiBridge] Error in detect_silences:", e)
            return {"success": False, "error": str(e)}

    def start_remove_silences(
        self,
        file_path: str,
        output_path: str,
        threshold_db: float = -40.0,
        min_duration_sec: float = 0.4,
        margin_ms: float = 80.0,
        normalize_audio: bool = True,
        sample_rate: int = 48000,
        output_format: str = "mp3",
        bitrate_kbps: int = 320
    ):
        """
        Starts the silence removal pipeline in a dedicated background worker thread.
        Emits progress and completion events to Javascript via evaluate_js.
        """
        output_path = self._resolve_output_path(output_path)
        log_info(f"[ApiBridge] start_remove_silences: {file_path} -> {output_path}")

        def run():
            def on_progress(pct: float, msg: str):
                if self._window:
                    self._emit_js("onSilenceProgress", pct, msg)

            try:
                res = self._silence_remover.remove_silences(
                    file_path=file_path,
                    output_path=output_path,
                    threshold_db=threshold_db,
                    min_duration_sec=min_duration_sec,
                    margin_ms=margin_ms,
                    normalize_audio=normalize_audio,
                    sample_rate=sample_rate,
                    output_format=output_format,
                    bitrate_kbps=bitrate_kbps,
                    progress_callback=on_progress
                )
                self._media_registry.register(res.get('path', output_path))
                if self._window:
                    self._emit_js("onSilenceComplete", res)
            except InterruptedError:
                if self._window:
                    self._emit_js("onSilenceCancelled")
            except Exception as e:
                log_error("[ApiBridge] Error during silence removal:", e)
                if self._window:
                    self._emit_js("onSilenceError", str(e))

        return self._start_job("silence", run, "onSilenceError")

    def cancel_silence_removal(self):
        """Cancels an in-flight silence removal process."""
        self._silence_remover.cancel()
        return {"status": "cancelling"}

    def inspect_dropped_images(self, file_paths: List[str]) -> List[Dict[str, Any]]:
        """
        Inspects image files dropped from the OS and parses their timestamp hints.
        """
        results = []
        for p in file_paths:
            if not os.path.isfile(p):
                continue
            self._media_registry.register(p)
            fname = os.path.basename(p)
            ts = parse_timestamp_from_filename(fname)
            file_stat = os.stat(p)
            file_size = file_stat.st_size
            size_str = f"{file_size / (1024 * 1024):.2f} MB" if file_size >= 1024 * 1024 else f"{file_size / 1024:.1f} KB"
            results.append({
                'path': p,
                'filename': fname,
                'parsed_timestamp': ts,
                'size_bytes': file_size,
                'size_formatted': size_str,
                'thumbnail_version': f"{file_stat.st_size:x}-{file_stat.st_mtime_ns:x}"
            })
        return results

    def align_timeline(self, image_paths: List[str], audio_path: str) -> Dict[str, Any]:
        """
        Maps images to audio timestamps and returns the aligned sequence.
        """
        log_info(f"[ApiBridge] align_timeline: {len(image_paths)} images, audio={audio_path}")
        try:
            if not os.path.exists(audio_path):
                return {"success": False, "error": f"Audio file not found: {audio_path}"}
            audio_info = get_audio_info(audio_path)
            aligned = self._timeline_aligner.align_images(image_paths, audio_info['duration'])
            return {
                "success": True,
                "audio": audio_info,
                "aligned_images": aligned,
                "total_images": len(aligned),
                "total_duration": audio_info['duration'],
                "total_duration_formatted": audio_info['duration_formatted']
            }
        except Exception as e:
            log_error("[ApiBridge] Error in align_timeline:", e)
            return {"success": False, "error": str(e)}

    def start_render_timeline_video(
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
        transition_duration: float = 0.5
    ):
        """
        Renders timeline draft video in a background worker thread.
        """
        output_path = self._resolve_output_path(output_path)
        log_info(f"[ApiBridge] start_render_timeline_video: {len(aligned_images)} imgs -> {output_path} ({width}x{height} @ {fps}fps)")

        def run():
            def on_progress(pct: float, msg: str):
                if self._window:
                    self._emit_js("onTimelineVideoProgress", pct, msg)

            try:
                res = self._timeline_aligner.render_video(
                    aligned_images=aligned_images,
                    audio_path=audio_path,
                    output_path=output_path,
                    width=width,
                    height=height,
                    fps=fps,
                    use_hardware_accel=use_hardware_accel,
                    zoom_type=zoom_type,
                    zoom_amount=zoom_amount,
                    transition_type=transition_type,
                    transition_duration=transition_duration,
                    progress_callback=on_progress
                )
                self._media_registry.register(res.get('path', output_path))
                if self._window:
                    self._emit_js("onTimelineVideoComplete", res)
            except InterruptedError:
                if self._window:
                    self._emit_js("onTimelineVideoCancelled")
            except Exception as e:
                log_error("[ApiBridge] Error during video render:", e)
                if self._window:
                    self._emit_js("onTimelineVideoError", str(e))

        return self._start_job("render", run, "onTimelineVideoError")

    def cancel_timeline_video(self):
        """Cancels an ongoing timeline video render."""
        self._timeline_aligner.cancel()
        return {"status": "cancelling"}

    def start_render_zoom_video(
        self,
        aligned_images: List[Dict[str, Any]],
        audio_path: str,
        output_path: str,
        width: int = 1920,
        height: int = 1080,
        fps: int = 30,
        use_hardware_accel: bool = True,
        zoom_type: str = "zoom_in",
        zoom_amount: float = 0.12,
        transition_type: str = "fade",
        transition_duration: float = 0.5
    ):
        """Render the separate Zoom & Transitions workflow with its own callbacks."""
        output_path = self._resolve_output_path(output_path)

        def run():
            def on_progress(pct: float, msg: str):
                if self._window:
                    self._emit_js("onZoomVideoProgress", pct, msg)
            try:
                result = self._timeline_aligner.render_video(
                    aligned_images=aligned_images,
                    audio_path=audio_path,
                    output_path=output_path,
                    width=width,
                    height=height,
                    fps=fps,
                    use_hardware_accel=use_hardware_accel,
                    zoom_type=zoom_type,
                    zoom_amount=zoom_amount,
                    transition_type=transition_type,
                    transition_duration=transition_duration,
                    progress_callback=on_progress
                )
                self._media_registry.register(result.get('path', output_path))
                if self._window:
                    self._emit_js("onZoomVideoComplete", result)
            except InterruptedError:
                if self._window:
                    self._emit_js("onZoomVideoCancelled")
            except Exception as e:
                log_error("[ApiBridge] Error during zoom/transition render:", e)
                if self._window:
                    self._emit_js("onZoomVideoError", str(e))

        return self._start_job("render", run, "onZoomVideoError")

    def export_timeline_csv(self, aligned_images: List[Dict[str, Any]], export_path: str) -> Dict[str, Any]:
        """Exports timeline markers as a CSV cue sheet."""
        try:
            export_path = self._resolve_output_path(export_path)
            self._timeline_aligner.export_csv_cue_sheet(aligned_images, export_path)
            return {"success": True, "path": export_path}
        except Exception as e:
            return {"success": False, "error": str(e)}
