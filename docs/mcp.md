# CapCap MCP

CapCap can run video workflows as a local MCP server over `stdio`. The MCP client starts the server and calls the same CapCap workflows used for media processing. The editor can stay closed.

## Connect a local agent

From the CapCap source directory, install the optional MCP SDK in the Python environment that already runs CapCap.

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-mcp.txt
```

Add a local stdio server in your agent's MCP configuration. For a client that uses an `mcpServers` JSON object, the entry looks like this. Adjust the paths for your checkout.

```json
{
  "mcpServers": {
    "capcap": {
      "command": "D:\\CodingTime\\CapCap\\.venv\\Scripts\\python.exe",
      "args": ["D:\\CodingTime\\CapCap\\app\\mcp_server.py"]
    }
  }
}
```

This entry point runs from the source checkout. It uses the local CapCap models and provider settings from the environment and the checkout's `.env`. Providers that rely on the Browser AI extension still require a connected browser.

## Tools

| Tool | Inputs and outputs |
| --- | --- |
| `capcap_inspect_video` | Video in. Audio and subtitle stream details and the reusable asset inventory out. |
| `capcap_list_resources` | Optional language in. Installed and missing models, voice IDs and availability out. |
| `capcap_extract_audio` | Video in. Original audio as a WAV out. |
| `capcap_extract_subtitles` | Video and a text subtitle stream index in. Extracted SRT out. |
| `capcap_separate_audio` | Audio file in. Vocals and background music as separate WAV files out. |
| `capcap_transcribe` | Video, source language and STT engine in. Original SRT and project path out. |
| `capcap_translate` | SRT and target language in. Translated SRT out. The agent can also choose a provider. |
| `capcap_generate_voice` | SRT and voice ID in. Dubbed WAV out. |
| `capcap_export` | Video and an SRT, dubbed audio or both in. MP4 out. |
| `capcap_process_video` | Video and processing options in. MP4 and project path out. Use `mode="both"` for subtitles and dubbing. |
| `capcap_job_status` | Job ID in. Current state, phase, phase progress and output paths out. |
| `capcap_cancel_job` | Job ID in. Cancel a queued or running job. |

Pass absolute paths to files on the machine running CapCap. Tools that inspect or process files return a `job_id`. Poll `capcap_job_status` until the state is `completed`, `failed` or `cancelled`. The `progress` value is per phase and can be empty when the engine provides no percentage.

### Find and reuse video assets

Start with `capcap_inspect_video`. Its `streams` list shows audio and embedded subtitle tracks. Use `capcap_extract_subtitles` with a stream marked `can_extract_srt` to extract a text track. Its `assets` list shows file paths, asset types, language when known, and whether each file came from a CapCap project or an MCP job.

Pass a returned file path directly to the next tool. CapCap keeps track of the source video across MCP jobs and omits indexed results if the source or an input file has changed. When starting with an external SRT or WAV, pass `video_path` to associate its result with the video. The agent chooses which asset to reuse based on its language and purpose.

`capcap_translate` lists the providers that produced the cues, which can differ from the requested provider after fallback. Full workflow results report the primary provider when the project records it. TTS results include the selected voice ID. When auto-language transcription exposes one detected language to the worker, the result reports it as `detected_language`. An empty value means the worker did not obtain a detection for that job.

### Choose OCR and providers per job

For `capcap_transcribe` or `capcap_process_video`, set `transcription_engine` to `ocr` to choose an OCR backend (`rapidocr` or `winocr`) and an optional rectangle. Coordinates are fractions of the original frame, measured from the top-left corner. For example, this reads the bottom quarter of the frame with Windows OCR.

```json
{
  "video_path": "D:\\Media\\clip.mp4",
  "transcription_engine": "ocr",
  "source_language": "vi",
  "ocr_backend": "winocr",
  "ocr_region": { "x": 0, "y": 0.75, "width": 1, "height": 0.25 }
}
```

For CapCut transcription, set `transcription_engine` to `capcut` and choose `capcut_backend` as `web` or `pc`. The PC mode uses the configured installation or CapCap's installation detection. In a full workflow, the choice also applies to CapCut dubbing when selected.

Choose a translation provider using `provider` in `capcap_translate`, or `translation_provider` in `capcap_process_video`. Available providers include `google`, `bing`, `google_ai_studio`, `openai`, `ollama`, `browser_ai`, `gemini_web` and `chatgpt_web`. Google and Bing use the web translator. AI providers require `use_ai` to be enabled. CapCut currently provides speech recognition and dubbing in CapCap. For translation, choose one of the listed translation providers.

Omitted options use the server's environment or the checkout's `.env`. Each request applies its overrides to its own worker.

For example, ask your agent **“Transcribe the video at D:\\Media\\clip.mp4, then export a video with those subtitles.”** The agent can use `capcap_transcribe`, wait for its SRT, then pass that file to `capcap_export`.

Each job gets its own output directory under `%LOCALAPPDATA%\CapCap\mcp` on Windows. To choose another location, add `--data-dir` and the absolute directory path to the server arguments. Jobs run sequentially within one server instance. Outputs and logs stay on disk after the session ends. Job IDs are tracked while that server session is open. The result includes `log_path` for diagnosing a failed job.
