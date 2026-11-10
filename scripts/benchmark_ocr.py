"""Benchmark one OCR implementation and save outputs for before/after comparison."""

import argparse
from contextlib import redirect_stdout
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import sys
import time
import zlib

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))


def compare_runs(reference, candidate):
    reference, candidate = Path(reference), Path(candidate)
    before = json.loads((reference / "report.json").read_text(encoding="utf-8"))
    after = json.loads((candidate / "report.json").read_text(encoding="utf-8"))
    frames_before = json.loads((reference / "frame-crc32.json").read_text(encoding="utf-8"))
    frames_after = json.loads((candidate / "frame-crc32.json").read_text(encoding="utf-8"))
    segments_before = json.loads((reference / "segments.json").read_text(encoding="utf-8"))
    segments_after = json.loads((candidate / "segments.json").read_text(encoding="utf-8"))
    config_keys = ("video", "backend", "sampling_fps", "language", "region", "rect", "start", "end", "warmed_engine")
    configuration_differences = [key for key in config_keys if before.get(key) != after.get(key)]
    return dict(
        configuration_matches=not configuration_differences, configuration_differences=configuration_differences,
        before_seconds=before["wall_seconds"], after_seconds=after["wall_seconds"],
        speedup=before["wall_seconds"] / after["wall_seconds"],
        time_saved_percent=(1 - after["wall_seconds"] / before["wall_seconds"]) * 100,
        input_frames_identical=frames_before == frames_after,
        frame_count_before=len(frames_before), frame_count_after=len(frames_after),
        subtitle_segments_identical=segments_before == segments_after,
        subtitle_segments_before=len(segments_before), subtitle_segments_after=len(segments_after),
        srt_identical=(reference / "subtitles.srt").read_bytes() == (candidate / "subtitles.srt").read_bytes(),
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("video", type=Path)
    parser.add_argument("--implementation", type=Path, default=ROOT / "app" / "ocr_processor.py")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--backend", choices=("winocr", "rapidocr"), default="winocr")
    parser.add_argument("--sampling-fps", default="2")
    parser.add_argument("--language", default="zh")
    parser.add_argument("--region", default="bottom")
    parser.add_argument("--rect", default="")
    parser.add_argument("--start", type=float, default=0)
    parser.add_argument("--end", type=float)
    parser.add_argument("--frame-reader", choices=("auto", "seek", "sequential"), default="auto")
    parser.add_argument("--compare-to", type=Path, help="Directory containing the reference run's report and outputs")
    args = parser.parse_args()
    if not args.video.is_file():
        parser.error("Video does not exist")
    args.output.mkdir(parents=True, exist_ok=True)
    os.environ.update(OCR_BACKEND=args.backend, OCR_SAMPLING_FPS=args.sampling_fps,
                      OCR_SUBTITLE_REGION=args.region, OCR_SUBTITLE_RECT=args.rect)
    implementation_sha256 = hashlib.sha256(args.implementation.read_bytes()).hexdigest()
    spec = importlib.util.spec_from_file_location("bench_ocr_impl", args.implementation)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    from translation.srt_utils import to_srt

    capture = module._open_video(str(args.video))
    fps = capture.get(module.cv2.CAP_PROP_FPS)
    frames = capture.get(module.cv2.CAP_PROP_FRAME_COUNT)
    metadata = dict(width=capture.get(module.cv2.CAP_PROP_FRAME_WIDTH), height=capture.get(module.cv2.CAP_PROP_FRAME_HEIGHT),
                    fps=fps, frames=frames, duration=frames / fps if fps else None)
    capture.set(module.cv2.CAP_PROP_POS_MSEC, args.start * 1000)
    ok, image = capture.read()
    capture.release()
    log_path = args.output / "ocr.log"
    with log_path.open("w", encoding="utf-8") as log, redirect_stdout(log):
        engine_started = time.perf_counter()
        engine = module._load_ocr_engine(backend=args.backend, lang=args.language)
        engine_init = time.perf_counter() - engine_started
        if ok:
            module.ocr_frame(engine, module.crop_subtitle_region(image, region=args.region))

    hashes = []
    original_crop = module.crop_subtitle_region

    def verified_crop(image, *positional, **kwargs):
        crop = original_crop(image, *positional, **kwargs)
        hashes.append(zlib.crc32(crop.tobytes()))
        return crop

    module.crop_subtitle_region = verified_crop
    last_progress = [-10]
    console = sys.stdout

    def progress(percent, message, *args):
        if percent >= last_progress[0] + 10:
            last_progress[0] = percent
            print(json.dumps({"progress": percent, "message": message}, ensure_ascii=True), file=console, flush=True)

    print(json.dumps({"stage": "start", "backend": args.backend, "sampling_fps": args.sampling_fps,
                      "video": metadata, "implementation": str(args.implementation)}, ensure_ascii=True), flush=True)
    started = time.perf_counter()
    with log_path.open("a", encoding="utf-8") as log, redirect_stdout(log):
        segments = module.transcribe_video_ocr(
            str(args.video), ocr_engine=engine, region=args.region, language=args.language,
            start_seconds=args.start, end_seconds=args.end, on_progress=progress,
            frame_read_mode=args.frame_reader,
        )
    elapsed = time.perf_counter() - started
    (args.output / "segments.json").write_text(json.dumps(segments, ensure_ascii=False, indent=2), encoding="utf-8")
    (args.output / "subtitles.srt").write_text(to_srt(segments), encoding="utf-8")
    (args.output / "frame-crc32.json").write_text(json.dumps(hashes), encoding="utf-8")
    log_text = log_path.read_text(encoding="utf-8")
    profiling = {key: float(value) for key, value in re.findall(r"^([^\n:]+): ([\d.]+)s$", log_text, re.M)}
    report = dict(video=str(args.video), video_metadata=metadata, backend=args.backend, sampling_fps=args.sampling_fps,
                  language=args.language, region=args.region, rect=args.rect, start=args.start, end=args.end,
                  engine_init_seconds=engine_init, warmed_engine=True, wall_seconds=elapsed,
                  verification_overhead="CRC32 of every cropped input frame included equally in both runs",
                  profiling=profiling, subtitle_segments=len(segments), sampled_frames=len(hashes),
                  implementation_sha256=implementation_sha256,
                  frame_read_mode_requested=args.frame_reader,
                  frame_read_strategy=next(iter(re.findall(r"^Frame reader: (.+)$", log_text, re.M)), "legacy_seek"))
    (args.output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"stage": "complete", **report}, ensure_ascii=True), flush=True)
    if args.compare_to:
        comparison = compare_runs(args.compare_to, args.output)
        (args.output / "comparison.json").write_text(json.dumps(comparison, indent=2), encoding="utf-8")
        print(json.dumps({"stage": "comparison", **comparison}), flush=True)
        if not all(comparison[key] for key in ("configuration_matches", "input_frames_identical", "subtitle_segments_identical", "srt_identical")):
            raise SystemExit(2)


if __name__ == "__main__":
    main()
