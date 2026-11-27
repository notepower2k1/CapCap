"""Exercise the real stdio protocol and the headless export worker."""

import asyncio
import os
from pathlib import Path
import subprocess
import sys

import pytest


pytest.importorskip("mcp")
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


ROOT = Path(__file__).resolve().parents[1]
FFMPEG = ROOT / "bin" / "ffmpeg" / "ffmpeg.exe"


def test_worker_applies_only_explicit_provider_and_ocr_overrides(monkeypatch):
    from app.mcp_worker import _apply_job_environment

    monkeypatch.setenv("OCR_SUBTITLE_RECT", "0,0.75,1,0.25")
    monkeypatch.setenv("CAPCUT_BACKEND", "web")
    monkeypatch.setenv("OPENAI_PROVIDER", "browser_ai")
    monkeypatch.setenv("AI_POLISHER_PROVIDER", "browser_ai")
    monkeypatch.setenv("OCR_BACKEND", "rapidocr")
    _apply_job_environment({"ocr_backend": None, "provider": None})
    assert os.environ["OCR_BACKEND"] == "rapidocr"
    assert os.environ["OPENAI_PROVIDER"] == "browser_ai"

    _apply_job_environment({
        "ocr_backend": "winocr", "ocr_region": {"x": 0, "y": 0.1, "width": 1, "height": 0.3},
        "capcut_backend": "pc", "provider": "bing",
    })
    assert os.environ["OCR_SUBTITLE_RECT"] == "0,0.1,1,0.3"
    assert os.environ["OCR_BACKEND"] == "winocr"
    assert os.environ["CAPCUT_BACKEND"] == "pc"
    assert os.environ["OPENAI_PROVIDER"] == os.environ["AI_POLISHER_PROVIDER"] == "bing"


def test_media_asset_lineage_is_reused_and_invalidated(monkeypatch, tmp_path):
    monkeypatch.syspath_prepend(str(ROOT / "app"))
    from mcp_assets import asset, inspect_video, job_provenance, publish_assets

    import mcp_assets
    monkeypatch.setattr(mcp_assets, "probe_video", lambda path: {"video_path": path})
    video = tmp_path / "video.mp4"
    video.write_bytes(b"video source")
    jobs = tmp_path / "jobs"
    original_dir = jobs / "original"
    translated_dir = jobs / "translated"
    voice_dir = jobs / "voice"
    for directory in (original_dir, translated_dir, voice_dir):
        directory.mkdir(parents=True)
    srt = original_dir / "original.srt"
    translated = translated_dir / "translated.srt"
    voice = voice_dir / "voice.wav"
    srt.write_bytes(b"original subtitle")
    translated.write_bytes(b"translated subtitle")
    voice.write_bytes(b"audio")

    publish_assets(original_dir, job_provenance({"video_path": video}, jobs), [asset("transcript", srt)])
    publish_assets(translated_dir, job_provenance({"srt_path": srt}, jobs),
                   [asset("translated_subtitles", translated, language="vi")])
    publish_assets(voice_dir, job_provenance({"srt_path": translated}, jobs), [asset("dubbed_audio", voice)])

    assert {item["kind"] for item in inspect_video(str(video), jobs)["assets"]} == {
        "transcript", "translated_subtitles", "dubbed_audio",
    }
    srt.write_bytes(b"edited original subtitle")
    assert inspect_video(str(video), jobs)["assets"] == []


@pytest.mark.skipif(not FFMPEG.is_file(), reason="Bundled FFmpeg required")
def test_mcp_stdio_exports_subtitled_video(tmp_path):
    video = tmp_path / "clip.mp4"
    srt = tmp_path / "source.srt"
    srt.write_text("1\n00:00:00,000 --> 00:00:00,900\nCapCap MCP test\n", encoding="utf-8")
    subprocess.run([
        str(FFMPEG), "-v", "error", "-f", "lavfi", "-i", "color=blue:s=320x180:d=1",
        "-c:v", "libx264", str(video),
    ], capture_output=True, check=True, timeout=20)

    async def exercise():
        params = StdioServerParameters(
            command=sys.executable,
            args=[str(ROOT / "app" / "mcp_server.py"), "--data-dir", str(tmp_path / "jobs")],
            env=dict(os.environ, CAPCAP_DEVICE="cpu"),
        )
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = {tool.name for tool in (await session.list_tools()).tools}
                assert {
                    "capcap_process_video", "capcap_job_status", "capcap_cancel_job",
                    "capcap_inspect_video", "capcap_list_resources", "capcap_extract_audio",
                    "capcap_extract_subtitles", "capcap_separate_audio",
                } <= tools
                invalid_region = await session.call_tool("capcap_transcribe", {
                    "video_path": str(video), "transcription_engine": "ocr",
                    "ocr_region": {"x": 0.6, "y": 0, "width": 0.5, "height": 1},
                })
                assert invalid_region.isError
                invalid = await session.call_tool("capcap_export", {"video_path": str(video)})
                assert invalid.isError
                queued = await session.call_tool("capcap_export", {"video_path": str(video), "srt_path": str(srt)})
                assert not queued.isError
                job = queued.structuredContent
                assert job["state"] == "queued"
                async with asyncio.timeout(40):
                    while job["state"] in {"queued", "running"}:
                        response = await session.call_tool("capcap_job_status", {"job_id": job["job_id"]})
                        assert not response.isError
                        job = response.structuredContent
                        await asyncio.sleep(0.1)
                assert job["state"] == "completed", job
                assert job["progress"] == 100
                output = Path(job["result"]["output_path"])
                assert output.stat().st_size > 0
                assert not srt.with_name("source_styled.ass").exists()

    asyncio.run(exercise())
