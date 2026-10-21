import os
import sys
import shutil
import subprocess

# Ensure project root is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.core.timeline_aligner import TimelineAligner, parse_timestamp_from_filename
from src.core.ffmpeg_utils import get_ffmpeg_path

def run_tests():
    print("Testing timestamp parsing...")
    assert parse_timestamp_from_filename("image-1_00:00.png") == 0.0
    assert parse_timestamp_from_filename("image-2_00:05.jpg") == 5.0
    assert parse_timestamp_from_filename("image-10_01:23.png") == 83.0
    assert parse_timestamp_from_filename("image_1_00:00.png") == 0.0
    assert parse_timestamp_from_filename("00:00.png") == 0.0
    assert parse_timestamp_from_filename("00_05.jpg") == 5.0
    assert parse_timestamp_from_filename("01_23.png") == 83.0
    assert parse_timestamp_from_filename("01:05:20.png") == 3920.0
    assert parse_timestamp_from_filename("00_15_500.png") == 15.5
    assert parse_timestamp_from_filename("scene_02_10.jpg") == 130.0
    assert parse_timestamp_from_filename("image-1_00_00.png") == 0.0
    assert parse_timestamp_from_filename("image-1_00_03_26") == 3.26
    assert parse_timestamp_from_filename("image-1_00_03_26.png") == 3.26
    assert parse_timestamp_from_filename("00_03_26.jpg") == 3.26
    assert parse_timestamp_from_filename("image-2_01_03_26.png") == 63.26
    print("Timestamp parsing tests passed!")

    print("\nTesting ambiguous HH_MM_SS timestamp inference...")
    # These mirror the real image archive convention. The legacy single-name
    # parser remains compatible, while a full audio timeline selects HH_MM_SS.
    assert parse_timestamp_from_filename("image-2_00_00_04.jpg") == 0.04
    timed_images = [
        "image-1_00_00_00.jpg",
        "image-2_00_00_04.jpg",
        "image-3_00_00_08.jpg",
        "image-280_00_12_23.jpg"
    ]
    timestamped_alignment = TimelineAligner().align_images(timed_images, 745.88)
    assert [item['start_sec'] for item in timestamped_alignment] == [0.0, 4.0, 8.0, 743.0]
    assert timestamped_alignment[-1]['end_sec'] == 745.88
    print("HH_MM_SS timestamp inference passed!")

    print("\nTesting alignment calculations...")
    aligner = TimelineAligner()
    images = [
        "C:/images/00_00.png",
        "C:/images/00_05.png",
        "C:/images/00_12.png"
    ]
    audio_dur = 20.0
    aligned = aligner.align_images(images, audio_dur)
    assert len(aligned) == 3
    assert aligned[0]['start_sec'] == 0.0
    assert aligned[0]['end_sec'] == 5.0
    assert aligned[0]['duration'] == 5.0

    assert aligned[1]['start_sec'] == 5.0
    assert aligned[1]['end_sec'] == 12.0
    assert aligned[1]['duration'] == 7.0

    assert aligned[2]['start_sec'] == 12.0
    assert aligned[2]['end_sec'] == 20.0
    assert aligned[2]['duration'] == 8.0
    print("Alignment calculations passed!")

    print("\nTesting still-image remapping through removed-silence intervals...")
    retimed = TimelineAligner.retime_aligned_images(
        [
            {'index': 0, 'path': 'a.jpg', 'filename': 'a.jpg', 'start_sec': 0.0, 'end_sec': 2.0, 'duration': 2.0},
            {'index': 1, 'path': 'b.jpg', 'filename': 'b.jpg', 'start_sec': 2.0, 'end_sec': 4.0, 'duration': 2.0},
        ],
        [(0.0, 1.5), (2.5, 4.0)],
    )
    assert len(retimed) == 2
    assert retimed[0]['start_sec'] == 0.0 and retimed[0]['end_sec'] == 1.5
    assert retimed[1]['start_sec'] == 1.5 and retimed[1]['end_sec'] == 3.0
    print("Still-image timeline remapping passed!")

    # Test rendering short video
    print("\nTesting video rendering with FFmpeg...")
    ffmpeg_bin = get_ffmpeg_path()
    test_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'scratch_timeline_test'))
    os.makedirs(test_dir, exist_ok=True)

    img1 = os.path.join(test_dir, "00_00_red.png")
    img2 = os.path.join(test_dir, "00_02_blue.png")
    test_audio = os.path.join(test_dir, "test_audio.wav")
    long_test_audio = os.path.join(test_dir, "long_test_audio.m4a")
    test_video = os.path.join(test_dir, "aligned_output.mp4")

    # Generate 2 solid color images
    subprocess.run([
        ffmpeg_bin, "-y", "-f", "lavfi", "-i", "color=c=red:s=1280x720:d=1",
        "-frames:v", "1", img1
    ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    subprocess.run([
        ffmpeg_bin, "-y", "-f", "lavfi", "-i", "color=c=blue:s=1280x720:d=1",
        "-frames:v", "1", img2
    ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    # Generate 4-second sine tone audio
    subprocess.run([
        ffmpeg_bin, "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=4",
        test_audio
    ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    # A small compressed silent track lets the long-image regression exercise
    # the complete 12:25.88 timestamp range without creating a huge WAV fixture.
    subprocess.run([
        ffmpeg_bin, "-y", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
        "-t", "745.88", "-c:a", "aac", "-b:a", "64k", long_test_audio
    ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    aligned_imgs = aligner.align_images([img1, img2], audio_duration=4.0)
    print("Aligned images:", aligned_imgs)

    progress_values = []

    def on_prog(pct, msg):
        progress_values.append(pct)
        print(f"[{pct:.1f}%] {msg}")

    result = aligner.render_video(
        aligned_images=aligned_imgs,
        audio_path=test_audio,
        output_path=test_video,
        width=1920,
        height=1080,
        fps=60,
        use_hardware_accel=True,
        zoom_type="zoom_in",
        zoom_amount=0.12,
        transition_type="fade",
        transition_duration=0.5,
        progress_callback=on_prog
    )

    print("\nVideo render complete:")
    print("Output:", result)
    assert os.path.exists(test_video)
    assert result['size_bytes'] > 1000
    assert result['resolution'] == '1920x1080'
    assert result['fps'] == 60
    assert any(pct > 10 for pct in progress_values), progress_values
    stream_probe = subprocess.run(
        [ffmpeg_bin, "-hide_banner", "-i", test_video],
        capture_output=True,
        text=True,
        check=False,
    )
    assert "Audio:" in stream_probe.stderr, stream_probe.stderr
    audio_sample = subprocess.run(
        [
            ffmpeg_bin, "-v", "error", "-ss", "1.0", "-i", test_video,
            "-map", "0:a:0", "-t", "0.5", "-f", "s16le",
            "-acodec", "pcm_s16le", "pipe:1",
        ],
        capture_output=True,
        check=True,
    ).stdout
    assert audio_sample and any(audio_sample), "Rendered audio track contains no audible samples"
    print("\nTIMELINE ALIGNER UNIT TESTS PASSED!")

    # Regression: render all 280 ordered images across the real 12:25.88
    # voiceover duration. The GPU backend must stream bounded batches while
    # maintaining timestamp intervals, transitions, zoom and audio duration.
    # The old filtergraph fallback is covered by the small test above; skip
    # this full-duration case when explicitly forcing that legacy path.
    if aligner._rust_renderer_path():
        for transition in ("fade", "dissolve", "wipeleft", "slideright", "circleopen", "fadeblack"):
            transition_output = os.path.join(test_dir, f"transition_{transition}.mp4")
            transition_result = aligner.render_video(
                aligned_images=aligned_imgs,
                audio_path=test_audio,
                output_path=transition_output,
                width=160,
                height=90,
                fps=6,
                use_hardware_accel=False,
                zoom_type="zoom_out",
                zoom_amount=0.12,
                transition_type=transition,
                transition_duration=0.5
            )
            assert transition_result['duration'] >= 3.9, (transition, transition_result)
        print("All six GPU transition shader paths passed.")

        many_image_video = os.path.join(test_dir, "many_image_output.mp4")
        long_duration = 745.88
        interval = long_duration / 280
        unique_image_dir = os.path.join(test_dir, "many_unique_images")
        os.makedirs(unique_image_dir, exist_ok=True)
        unique_image_paths = []
        for index in range(280):
            source_image = img1 if index % 2 == 0 else img2
            unique_path = os.path.join(unique_image_dir, f"still_{index:03d}.png")
            if not os.path.exists(unique_path):
                try:
                    os.link(source_image, unique_path)
                except OSError:
                    shutil.copyfile(source_image, unique_path)
            unique_image_paths.append(unique_path)
        many_images = []
        for index in range(280):
            start = index * interval
            end = long_duration if index == 279 else (index + 1) * interval
            many_images.append({
                **aligned_imgs[index % 2],
                'path': unique_image_paths[index],
                'filename': os.path.basename(unique_image_paths[index]),
                'start_sec': start,
                'end_sec': end,
                'duration': end - start,
            })
        many_result = aligner.render_video(
            aligned_images=many_images,
            audio_path=long_test_audio,
            output_path=many_image_video,
            width=160,
            height=90,
            fps=6,
            use_hardware_accel=True,
            zoom_type="zoom_in",
            zoom_amount=0.12,
            transition_type="fade",
            transition_duration=0.04
        )
        assert os.path.exists(many_image_video)
        assert many_result['image_count'] == 280
        assert many_result['size_bytes'] > 1000
        assert abs(many_result['duration'] - long_duration) < 0.5, (
            many_result['duration'], long_duration
        )

        def sample_rgb(time_sec):
            sampled = subprocess.run(
                [
                    ffmpeg_bin, "-v", "error", "-ss", f"{time_sec:.3f}",
                    "-i", many_image_video, "-frames:v", "1",
                    "-vf", "scale=1:1:flags=area,format=rgb24",
                    "-f", "rawvideo", "pipe:1"
                ],
                check=True,
                capture_output=True
            ).stdout
            assert len(sampled) == 3, f"Could not sample output frame at {time_sec:.3f}s"
            return tuple(sampled)

        for image_index in (0, 1, 278, 279):
            rgb = sample_rgb(image_index * interval + 0.5)
            if image_index % 2 == 0:
                assert rgb[0] > rgb[2] * 2, (image_index, rgb)
            else:
                assert rgb[2] > rgb[0] * 2, (image_index, rgb)
        print(f"280-image timestamp/order regression passed ({many_result['duration']:.2f}s output).")

        # Long hardware gate is opt-in because it encodes 44,000+ frames. Use
        # VIDEOHELPER_TEST_FULL_1080P_DIRECT=1 when validating the real export
        # route or collecting a throughput benchmark on the target GPU.
        if os.environ.get("VIDEOHELPER_TEST_FULL_1080P_DIRECT") == "1":
            import time

            hd_output = os.path.join(test_dir, "many_image_1080p60_output.mp4")
            started = time.perf_counter()
            hd_result = aligner.render_video(
                aligned_images=many_images,
                audio_path=long_test_audio,
                output_path=hd_output,
                width=1920,
                height=1080,
                fps=60,
                use_hardware_accel=True,
                zoom_type="zoom_in",
                zoom_amount=0.12,
                transition_type="fade",
                transition_duration=0.5,
            )
            elapsed = time.perf_counter() - started
            assert hd_result['resolution'] == '1920x1080'
            assert hd_result['fps'] == 60
            assert abs(hd_result['duration'] - long_duration) < 0.5, hd_result
            print(
                f"1080p60 D3D12/NVENC full render passed in {elapsed:.1f}s "
                f"({long_duration / elapsed:.2f}x real time)."
            )
            for image_index in (0, 1, 278, 279):
                rgb = subprocess.run(
                    [
                        ffmpeg_bin, "-v", "error", "-ss", f"{image_index * interval + 0.5:.3f}",
                        "-i", hd_output, "-frames:v", "1",
                        "-vf", "scale=1:1:flags=area,format=rgb24",
                        "-f", "rawvideo", "pipe:1",
                    ],
                    check=True,
                    capture_output=True,
                ).stdout
                assert len(rgb) == 3
                if image_index % 2 == 0:
                    assert rgb[0] > rgb[2] * 2, (image_index, tuple(rgb))
                else:
                    assert rgb[2] > rgb[0] * 2, (image_index, tuple(rgb))
    else:
        print("280-image render regression skipped: Rust/WGPU renderer is disabled or not built.")

if __name__ == '__main__':
    run_tests()
