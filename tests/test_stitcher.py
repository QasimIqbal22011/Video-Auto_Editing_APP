import os
import sys
import subprocess

# Ensure project root in sys.path
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from src.core.ffmpeg_utils import get_ffmpeg_path, get_audio_info
from src.core.audio_stitcher import AudioStitcher

def main():
    ffmpeg_bin = get_ffmpeg_path()
    print("Located FFmpeg at:", ffmpeg_bin)

    test_dir = os.path.join(BASE_DIR, "scratch_test")
    os.makedirs(test_dir, exist_ok=True)
    t1 = os.path.join(test_dir, "test_clip1.wav")
    t2 = os.path.join(test_dir, "test_clip2.wav")
    out_master = os.path.join(test_dir, "stitched_master_test.mp3")

    # Generate 1.5s 440Hz and 2.0s 880Hz audio clips
    subprocess.run([ffmpeg_bin, "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=1.5", t1], check=True, capture_output=True)
    subprocess.run([ffmpeg_bin, "-y", "-f", "lavfi", "-i", "sine=frequency=880:duration=2.0", t2], check=True, capture_output=True)

    info1 = get_audio_info(t1)
    info2 = get_audio_info(t2)
    print(f"Clip 1 metadata: duration={info1['duration']}s, sr={info1['sample_rate']}Hz, fmt={info1['format_name']}")
    print(f"Clip 2 metadata: duration={info2['duration']}s, sr={info2['sample_rate']}Hz, fmt={info2['format_name']}")

    stitcher = AudioStitcher()

    def progress(pct, msg):
        print(f"[{pct:5.1f}%] {msg}")

    # Stitch clips with 0.5s pause between them and YouTube loudnorm
    res = stitcher.stitch(
        file_paths=[t1, t2],
        output_path=out_master,
        gap_sec=0.5,
        normalize_audio=True,
        sample_rate=48000,
        output_format="mp3",
        bitrate_kbps=320,
        progress_callback=progress
    )

    print("\nSUCCESS! Master audio file generated:")
    print(f"File: {res['filename']}")
    print(f"Duration: {res['duration']}s ({res['duration_formatted']})")
    print(f"Sample Rate: {res['sample_rate']}Hz")
    print(f"Size: {res['size_formatted']}")
    print(f"Path: {res['path']}")

    # Regression: hundreds of inputs previously created a Windows command line
    # longer than CreateProcess permits. Repeating one tiny source keeps this
    # integration test fast while still exercising all 312 input slots.
    short_clip = os.path.join(test_dir, "short_clip.wav")
    many_clip_output = os.path.join(test_dir, "many_clip_output.wav")
    subprocess.run(
        [ffmpeg_bin, "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=0.05", short_clip],
        check=True,
        capture_output=True
    )
    many_result = stitcher.stitch(
        file_paths=[short_clip] * 312,
        output_path=many_clip_output,
        gap_sec=0.01,
        normalize_audio=False,
        sample_rate=48000,
        output_format="wav",
        progress_callback=progress
    )
    expected_duration = (312 * 0.05) + (311 * 0.01)
    assert abs(many_result['duration'] - expected_duration) < 0.25, (
        many_result['duration'], expected_duration
    )
    print(f"312-clip batching regression passed ({many_result['duration']:.2f}s output).")

    # Clean up test files
    for f in [t1, t2, out_master, short_clip, many_clip_output]:
        if os.path.exists(f):
            try:
                os.remove(f)
            except Exception:
                pass
    try:
        os.rmdir(test_dir)
    except Exception:
        pass
    print("\nAll integration checks passed cleanly!")

if __name__ == '__main__':
    main()
