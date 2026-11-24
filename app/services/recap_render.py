"""Build a self-contained recap from ordered source scenes and narration WAVs."""
from dataclasses import dataclass
from fractions import Fraction
from hashlib import sha256
import json
from math import ceil
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
from uuid import uuid4

from runtime_paths import bin_path, subprocess_hidden_kwargs
from translation.video_recap import estimate_seconds
from .recap_voice import current_clip_path


@dataclass(frozen=True)
class RecapClip:
    start_ms: int
    end_ms: int
    audio: Path
    audio_ms: int

def scene_range(segment):
    scene = segment.get("scene")
    return scene if isinstance(scene, dict) else None


def upgrade_scene_ranges(draft):
    """Retain old clip lists while adopting their first clip as the scene."""
    for segment in draft["segments"]:
        clips = segment.get("clips")
        if isinstance(clips, list):
            segment["legacy_clips"] = segment.pop("clips")
            if clips and isinstance(clips[0], dict):
                segment["scene"] = {**clips[0], "selection": "manual"}
            else:
                segment.pop("scene", None)


def range_duration(scene, source_duration_ms):
    if not isinstance(scene, dict):
        return None
    start, end = scene.get("start_ms"), scene.get("end_ms")
    if type(start) is not int or type(end) is not int or not 0 <= start < end <= source_duration_ms:
        return None
    return end - start if end - start >= 100 else None


def scene_duration(segment, source_duration_ms):
    return range_duration(scene_range(segment), source_duration_ms)


def suggest_scene_ranges(draft, rows, project_root, source_duration_ms, *,
                         reset_index=None, reselect_automatic=False):
    """Fill or resize automatic scenes. An authored range always wins."""
    if source_duration_ms < 100:
        return False
    pending, occupied = [], []
    voice = draft.get("voice_id")
    avoid_overlap = draft.get("avoid_scene_overlap", True)
    for index, segment in enumerate(draft["segments"]):
        scene = scene_range(segment)
        chosen = isinstance(scene, dict) and bool(scene)
        automatic = chosen and scene.get("selection") == "auto"
        current = current_clip_path(project_root, segment, voice)
        speech_ms = segment["audio"]["duration_ms"] if current else estimate_seconds([segment]) * 1000
        length = min(source_duration_ms, max(100, speech_ms))
        valid = range_duration(scene, source_duration_ms) is not None
        # Older drafts could save reversed or incomplete markers without a selection tag.
        invalid_legacy = chosen and "selection" not in scene and not valid
        if reset_index is not None:
            should_suggest = index == reset_index
        else:
            should_suggest = (not chosen or invalid_legacy or (automatic and (
                reselect_automatic or not valid or scene["end_ms"] - scene["start_ms"] != length
            )))
        if should_suggest:
            pending.append((segment, length))
        elif valid:
            occupied.append((scene["start_ms"], scene["end_ms"]))
    if not pending:
        return False

    by_time = {max(0, round(row["start"] * 1000)) for row in rows}
    changed = False
    for segment, length in pending:
        # Confirm that each evidence timestamp still belongs to an original cue.
        anchors = [quote["time_ms"] for quote in segment["evidence"]
                   if quote["time_ms"] in by_time and quote["time_ms"] < source_duration_ms]
        lead_in = min(220, length // 5)
        candidates = [max(0, min(ms - lead_in, source_duration_ms - length))
                      for ms in anchors]
        if not candidates:
            continue

        def overlap(start):
            return sum(max(0, min(start + length, end) - max(start, left)) for left, end in occupied)

        start = min(candidates, key=overlap) if avoid_overlap else candidates[0]
        scene = {"start_ms": start, "end_ms": start + length, "selection": "auto"}
        if segment.get("scene") != scene:
            segment["scene"] = scene
            changed = True
        occupied.append((start, start + length))
    return changed


def recover_scene_range(draft, index, rows, project_root, source_duration_ms):
    """Rebuild the suggested cut for a legacy edit without changing the draft."""
    segments = list(draft["segments"])
    segments[index] = {**segments[index], "scene": None}
    candidate = {**draft, "segments": segments}
    suggest_scene_ranges(candidate, rows, project_root, source_duration_ms, reset_index=index)
    return scene_range(segments[index])


def render_plan(draft, project_root, source_duration_ms):
    ready = []
    for segment in draft["segments"]:
        audio = current_clip_path(project_root, segment, draft.get("voice_id"))
        if audio is None or scene_duration(segment, source_duration_ms) is None:
            continue
        scene = scene_range(segment)
        ready.append(RecapClip(scene["start_ms"], scene["end_ms"], audio, segment["audio"]["duration_ms"]))
    return ready


def recap_preview_path(project_root, source, clips):
    """Identify a rendered preview by the exact media and ordered cuts it uses."""
    def media_signature(path):
        path = Path(path).resolve()
        info = path.stat()
        return str(path), info.st_mtime_ns, info.st_size

    try:
        signature = [1, media_signature(source), [
            (clip.start_ms, clip.end_ms, clip.audio_ms, media_signature(clip.audio)) for clip in clips
        ]]
    except (OSError, ValueError):
        return None
    digest = sha256(json.dumps(signature, separators=(",", ":")).encode("utf-8")).hexdigest()
    return Path(project_root).resolve() / "analysis" / "recap_preview" / f"preview-{digest}.mp4"


def _run(command, is_canceled, *, cwd=None):
    if is_canceled():
        raise InterruptedError()
    with subprocess.Popen(command, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          **subprocess_hidden_kwargs()) as process:
        while True:
            try:
                stdout, stderr = process.communicate(timeout=.2)
                break
            except subprocess.TimeoutExpired:
                if is_canceled():
                    process.kill()
                    process.communicate()
                    raise InterruptedError()
        if is_canceled():
            raise InterruptedError()
        if process.returncode:
            raise RuntimeError(stderr.decode("utf-8", errors="replace").strip()[-1500:])
        return stdout


def _output_path(source, output_dir):
    directory = Path(output_dir).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    return directory / f"{Path(source).stem[:80]}_recap_{uuid4().hex[:8]}.mp4"


def save_recap_preview(preview, source, output_dir, *, progress, is_canceled):
    if is_canceled():
        raise InterruptedError()
    destination = _output_path(source, output_dir)
    progress(0, 1)
    with TemporaryDirectory(prefix="capcap-recap-", dir=destination.parent, ignore_cleanup_errors=True) as temporary:
        staged = Path(temporary) / "recap.mp4"
        with Path(preview).open("rb") as original, staged.open("wb") as output:
            while chunk := original.read(1024 * 1024):
                if is_canceled():
                    raise InterruptedError()
                output.write(chunk)
        if is_canceled():
            raise InterruptedError()
        staged.replace(destination)
    progress(1, 1)
    return str(destination)


def recap_frame_rate(source, is_canceled):
    ffprobe = bin_path("ffmpeg", "ffprobe.exe")
    try:
        fps = Fraction(_run([ffprobe, "-v", "error", "-select_streams", "v:0",
                            "-show_entries", "stream=avg_frame_rate", "-of", "csv=p=0", str(source)],
                            is_canceled).decode("ascii").strip())
    except InterruptedError:
        raise
    except (OSError, RuntimeError, ValueError, ZeroDivisionError):
        fps = Fraction(30)
    return fps if 1 <= fps <= 120 else Fraction(30)


def _clip_duration(clip, fps):
    frames = ceil(Fraction(max(clip.end_ms - clip.start_ms, clip.audio_ms), 1000) * fps)
    return frames / fps


def recap_subtitles(clips, narrations, fps):
    rows = []
    start = Fraction(0)
    for clip, narration in zip(clips, narrations, strict=True):
        rows.append({
            "start": float(start),
            "end": float(start + Fraction(clip.audio_ms, 1000)),
            "text": narration,
        })
        start += _clip_duration(clip, fps)
    return rows


def render_recap_video(source, clips, output_dir, *, progress, is_canceled, fps=None):
    if not clips:
        raise ValueError("No recap scenes are ready.")
    source = Path(source).resolve()
    if not source.is_file():
        raise FileNotFoundError(source)
    ffmpeg = bin_path("ffmpeg", "ffmpeg.exe")
    fps = fps or recap_frame_rate(source, is_canceled)

    destination = _output_path(source, output_dir)
    base_command = [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y"]
    with TemporaryDirectory(prefix="capcap-recap-", dir=destination.parent, ignore_cleanup_errors=True) as temporary:
        workspace = Path(temporary)
        entries = ["ffconcat version 1.0"]
        total = len(clips) + 1
        progress(0, total)
        for index, clip in enumerate(clips, 1):
            length = clip.end_ms - clip.start_ms
            duration = float(_clip_duration(clip, fps))
            name = f"scene-{index:03d}.mkv"
            _run(base_command + [
                "-ss", f"{clip.start_ms / 1000:.3f}", "-t", f"{length / 1000:.3f}", "-threads", "2",
                "-i", str(source), "-i", str(clip.audio), "-map", "0:v:0", "-map", "1:a:0",
                "-vf", f"fps={fps},scale=trunc(iw/2)*2:trunc(ih/2)*2,tpad=stop_mode=clone:stop=-1",
                "-af", "apad", "-t", f"{duration:.8f}",
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
                "-c:a", "pcm_s16le", "-ar", "48000", "-ac", "1", str(workspace / name),
            ], is_canceled)
            entries.extend((f"file {name}", f"duration {duration:.8f}"))
            progress(index, total)
        (workspace / "list.ffconcat").write_text("\n".join(entries) + "\n", encoding="ascii")
        staged = workspace / "recap.mp4"
        _run(base_command + ["-f", "concat", "-safe", "1", "-i", "list.ffconcat",
                              "-c:v", "copy", "-c:a", "aac", "-b:a", "160k",
                              "-movflags", "+faststart", str(staged)], is_canceled, cwd=workspace)
        if is_canceled():
            raise InterruptedError()
        staged.replace(destination)
        progress(total, total)
    return str(destination)
