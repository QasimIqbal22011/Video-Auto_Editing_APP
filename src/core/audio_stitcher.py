import os
import sys
import re
import subprocess
import tempfile
import threading
from typing import List, Dict, Any, Optional, Callable
from .ffmpeg_utils import get_ffmpeg_path, get_audio_info

class AudioStitcher:
    """
    High-performance audio stitcher designed for YouTube Automation content creation.
    Concatenates multiple audio files into a unified master audio file with:
    - Customizable pause/gap duration between clips
    - YouTube audio loudness normalization (EBU R128 / loudnorm)
    - Resampling to uniform sample rate (44.1kHz or 48kHz)
    - Multi-format support (MP3, WAV, AAC, M4A, FLAC, OGG)
    - Live progress tracking and graceful cancellation
    """

    # Keep both the Windows process command line and FFmpeg filtergraph bounded.
    _MAX_INPUTS_PER_BATCH = 24

    def __init__(self):
        self._current_process: Optional[subprocess.Popen] = None
        self._is_cancelled = False
        self._lock = threading.Lock()

    def cancel(self):
        """Cancels an ongoing stitching process."""
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

    def stitch(
        self,
        file_paths: List[str],
        output_path: str,
        gap_sec: float = 0.3,
        normalize_audio: bool = True,
        sample_rate: int = 48000,
        output_format: str = "mp3",
        bitrate_kbps: int = 320,
        progress_callback: Optional[Callable[[float, str], None]] = None
    ) -> Dict[str, Any]:
        """
        Executes the stitching pipeline.
        
        Args:
            file_paths: Ordered list of absolute paths to audio files
            output_path: Target destination path for master audio
            gap_sec: Duration in seconds to insert between clips (e.g. 0.3s)
            normalize_audio: If True, applies YouTube standard EBU R128 loudnorm
            sample_rate: Output sample rate (44100 or 48000)
            output_format: 'mp3', 'wav', 'm4a', 'aac', or 'flac'
            bitrate_kbps: Audio bitrate for lossy formats (e.g. 320 or 192)
            progress_callback: callback(percent_0_to_100, status_message)
            
        Returns:
            dict with metadata of the output file
        """
        self._is_cancelled = False
        
        if not file_paths:
            raise ValueError("No audio files provided for stitching.")
            
        if len(file_paths) == 0:
            raise ValueError("Audio file list is empty.")

        gap_sec = max(0.0, float(gap_sec))
        sample_rate = int(sample_rate)
        if not 8000 <= sample_rate <= 192000:
            raise ValueError("sample_rate must be between 8000 and 192000 Hz")
        bitrate_kbps = max(8, int(bitrate_kbps))
        output_format = str(output_format).lower().lstrip('.')
        if output_format not in {"mp3", "wav", "m4a", "aac", "flac"}:
            raise ValueError(f"Unsupported output format: {output_format}")

        # Ensure output directory exists
        out_dir = os.path.dirname(os.path.abspath(output_path))
        os.makedirs(out_dir, exist_ok=True)

        if progress_callback:
            progress_callback(5.0, "Analyzing audio clips...")

        # Analyze clips & compute total expected duration
        total_clip_duration = 0.0
        clip_infos = []
        for i, fp in enumerate(file_paths):
            if self._is_cancelled:
                raise InterruptedError("Stitching cancelled by user.")
            info = get_audio_info(fp)
            clip_infos.append(info)
            total_clip_duration += info['duration']

        total_gaps = max(0, len(file_paths) - 1) * gap_sec
        total_expected_duration = total_clip_duration + total_gaps
        if total_expected_duration <= 0:
            total_expected_duration = 1.0

        ffmpeg_bin = get_ffmpeg_path()
        out_fmt = output_format.lower().replace('.', '')

        def add_encoder_options(command: list[str]) -> None:
            if out_fmt == "mp3":
                command.extend(["-c:a", "libmp3lame", "-b:a", f"{bitrate_kbps}k"])
            elif out_fmt == "wav":
                command.extend(["-c:a", "pcm_s16le"])
            elif out_fmt in ("m4a", "aac"):
                command.extend(["-c:a", "aac", "-b:a", f"{bitrate_kbps}k"])
            elif out_fmt == "flac":
                command.extend(["-c:a", "flac"])
            else:
                command.extend(["-c:a", "libmp3lame", "-b:a", f"{bitrate_kbps}k"])
            command.extend(["-ar", str(sample_rate), output_path])

        def run_ffmpeg(command: list[str], progress_start: float, progress_span: float,
                       duration_offset: float, status_prefix: str) -> None:
            if self._is_cancelled:
                raise InterruptedError("Stitching cancelled by user.")
            startupinfo = None
            if sys.platform == "win32":
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                startupinfo.wShowWindow = 0
            try:
                proc = subprocess.Popen(
                    command,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    encoding='utf-8',
                    errors='replace',
                    startupinfo=startupinfo,
                    bufsize=1
                )
            except OSError as exc:
                if getattr(exc, "winerror", None) == 206:
                    raise RuntimeError(
                        "Windows rejected the FFmpeg command as too long. "
                        "Try a shorter output path or fewer clips per batch."
                    ) from exc
                raise

            with self._lock:
                self._current_process = proc
            try:
                time_regex = re.compile(r"time=(\d+):(\d+):(\d+\.?\d*)")
                for line in proc.stderr:
                    if self._is_cancelled:
                        proc.terminate()
                        raise InterruptedError("Stitching cancelled by user.")
                    match = time_regex.search(line)
                    if match and progress_callback:
                        hours, minutes = int(match.group(1)), int(match.group(2))
                        current_time = hours * 3600 + minutes * 60 + float(match.group(3))
                        overall_time = min(total_expected_duration, duration_offset + current_time)
                        progress_pct = progress_start + (overall_time / total_expected_duration) * progress_span
                        progress_callback(
                            round(min(96.0, progress_pct), 1),
                            f"{status_prefix} ({overall_time:.1f}s / {total_expected_duration:.1f}s)"
                        )
                proc.wait()
                if proc.returncode != 0 and not self._is_cancelled:
                    raise RuntimeError(f"FFmpeg stitching failed with exit code {proc.returncode}")
            finally:
                with self._lock:
                    self._current_process = None

        if progress_callback:
            progress_callback(5.0, "Preparing bounded audio batches...")

        # A single FFmpeg invocation with hundreds of inputs exceeds Windows'
        # CreateProcess command-line limit. Render short PCM batches, then join
        # those batch files in one final pass. Gaps are emitted at each original
        # clip boundary, and loudness normalization/encoding still happen once.
        with tempfile.TemporaryDirectory(prefix="vhstitch_") as temp_dir:
            batch_paths = []
            chunks = [
                file_paths[offset:offset + self._MAX_INPUTS_PER_BATCH]
                for offset in range(0, len(file_paths), self._MAX_INPUTS_PER_BATCH)
            ]
            processed_duration = 0.0
            for batch_index, chunk in enumerate(chunks):
                chunk_start = batch_index * self._MAX_INPUTS_PER_BATCH
                filters, labels = [], []
                element_count = 0
                chunk_duration = 0.0
                for local_index, fp in enumerate(chunk):
                    global_index = chunk_start + local_index
                    label = f"in{local_index}"
                    filters.append(
                        f"[{local_index}:a]aresample={sample_rate},"
                        f"aformat=sample_fmts=fltp:channel_layouts=stereo[{label}]"
                    )
                    labels.append(f"[{label}]")
                    element_count += 1
                    chunk_duration += clip_infos[global_index]['duration']
                    if gap_sec > 0.001 and global_index < len(file_paths) - 1:
                        gap_label = f"gap{local_index}"
                        filters.append(
                            f"anullsrc=r={sample_rate}:cl=stereo,"
                            f"atrim=0:{gap_sec:.3f}[{gap_label}]"
                        )
                        labels.append(f"[{gap_label}]")
                        element_count += 1
                        chunk_duration += gap_sec
                filters.append(
                    "".join(labels) + f"concat=n={element_count}:v=0:a=1[outa]"
                )
                batch_path = os.path.join(temp_dir, f"batch_{batch_index:04d}.wav")
                batch_paths.append(batch_path)
                command = [ffmpeg_bin, "-y", "-hide_banner"]
                for fp in chunk:
                    command.extend(["-i", fp])
                command.extend([
                    "-filter_complex", ";".join(filters), "-map", "[outa]",
                    "-c:a", "pcm_s16le", "-ar", str(sample_rate), "-ac", "2", batch_path
                ])
                if progress_callback:
                    progress_callback(
                        15.0 + (processed_duration / total_expected_duration) * 45.0,
                        f"Preparing audio batch {batch_index + 1} of {len(chunks)}..."
                    )
                run_ffmpeg(
                    command, 15.0, 45.0, processed_duration,
                    f"Preparing audio batch {batch_index + 1} of {len(chunks)}..."
                )
                processed_duration += chunk_duration

            manifest_path = os.path.join(temp_dir, "batches.ffconcat")
            with open(manifest_path, "w", encoding="utf-8", newline="\n") as manifest:
                manifest.write("ffconcat version 1.0\n")
                for batch_path in batch_paths:
                    manifest.write(f"file '{os.path.basename(batch_path)}'\n")

            final_filter = "-af" if normalize_audio else None
            command = [
                ffmpeg_bin, "-y", "-hide_banner", "-f", "concat", "-safe", "0",
                "-i", manifest_path, "-map", "0:a:0"
            ]
            if final_filter:
                command.extend([final_filter, "loudnorm=I=-16:TP=-1.5:LRA=11"])
            add_encoder_options(command)
            if progress_callback:
                progress_callback(60.0, "Joining batches and mastering final audio...")
            run_ffmpeg(command, 60.0, 36.0, 0.0, "Final audio mastering...")

        if self._is_cancelled:
            if os.path.exists(output_path):
                try:
                    os.remove(output_path)
                except Exception:
                    pass
            raise InterruptedError("Stitching cancelled by user.")

        if progress_callback:
            progress_callback(100.0, "Master audio ready!")

        # Retrieve final output file info
        final_info = get_audio_info(output_path)
        return final_info
