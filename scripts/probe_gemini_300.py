"""Send one Gemini request using 300 generated cues and CapCap's translation prompt."""

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "app"))

from openai import OpenAI
from translation.orchestrator import TranslationOrchestrator
from translation.prompt_builder import build_translation_messages
from translation.srt_utils import parse_numbered_line_items, to_srt, validate_texts

OUTPUT_DIR = ROOT / "temp" / "gemini-300-probe"
BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"
DEFAULT_CONFIG = {
    "api_key": "",
    "model": "gemini-2.5-pro",
    "max_tokens": 65536,
    "reasoning_effort": "medium",
    "timeout_seconds": 300,
}


def generate_mock_subtitles() -> list[dict]:
    dialogues = [
        "李明：小雨，关于{topic}，我们放学以后再讨论吧。",
        "小雨：我已经看过资料了，不过还有几个地方不太明白。",
        "李明：别着急，我们一起把问题整理清楚。",
        "小雨：谢谢你，你总是在我需要的时候帮助我。",
        "张老师：这次{topic}很重要，希望大家认真准备。",
        "李明：老师，我们想先听听其他同学的意见。",
        "张老师：当然可以，每个人都应该有表达想法的机会。",
        "小雨：如果时间来不及，我们可以先完成最重要的部分。",
        "李明：这个办法不错，我来负责整理最后的结果。",
        "小雨：那就这样决定了，明天我们在图书馆见。",
    ]
    topics = ["下周的考试", "我们的研究报告", "学校的演讲比赛", "新学期的活动", "周末的旅行计划"]
    return [
        {"start": index * 3.0, "end": index * 3.0 + 2.8,
         "text": f"第{index // 10 + 1}天，" + dialogues[index % 10].format(topic=topics[index // 10 % 5])}
        for index in range(300)
    ]


def probe_gemini_300(api_key: str, model: str = "gemini-2.5-pro", max_tokens: int = 65536,
                     reasoning_effort: str = "medium", timeout_seconds: int = 300,
                     output_dir: Path = OUTPUT_DIR) -> dict:
    """Save a request, raw response, and diagnostics without saving the API key."""
    if not str(api_key or "").strip():
        raise ValueError("API key is empty.")
    if reasoning_effort not in ("low", "medium", "high"):
        raise ValueError("reasoning_effort must be low, medium, or high.")
    if int(max_tokens) <= 0 or int(timeout_seconds) <= 0:
        raise ValueError("max_tokens and timeout_seconds must be positive.")
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    segments = generate_mock_subtitles()
    source_texts = [segment["text"] for segment in segments]
    (output_dir / "mock_300.srt").write_text(to_srt(segments), encoding="utf-8")
    system_msg, user_msg = build_translation_messages(
        source_texts=source_texts, translated_texts=None, src_lang="zh-Hans", target_lang="vi",
    )
    kwargs = {
        "model": model,
        "messages": [{"role": "system", "content": system_msg}, {"role": "user", "content": user_msg}],
        "max_tokens": max(2048, int(max_tokens)),
        "reasoning_effort": reasoning_effort,
        "timeout": int(timeout_seconds),
    }
    (output_dir / "request.json").write_text(json.dumps(kwargs, ensure_ascii=False, indent=2), encoding="utf-8")
    batches, single_pass = TranslationOrchestrator._build_ai_batches(
        source_texts=source_texts, translated_texts=None, requested_max_segments=80,
        provider_type="google_ai_studio",
    )
    report = {
        "status": "pending", "model_requested": model, "input_cues": 300,
        "request_count": 1, "sdk_max_retries": 0,
        "max_tokens": kwargs["max_tokens"], "reasoning_effort": reasoning_effort,
        "timeout_seconds": int(timeout_seconds), "prompt_characters": len(system_msg) + len(user_msg),
        "app_batch_plan_for_same_input": {
            "single_pass": single_pass, "batch_sizes": [len(batch[0]) for batch in batches],
            "max_tokens_per_batch": [batch[2] for batch in batches],
        },
    }
    started = time.monotonic()
    text = ""
    try:
        # One request makes the first API failure visible without automatic retry or fallback.
        with OpenAI(api_key=api_key, base_url=BASE_URL, max_retries=0) as client:
            response = client.chat.completions.create(**kwargs)
        choice = response.choices[0] if response.choices else None
        text = (choice.message.content or "") if choice else ""
        items = parse_numbered_line_items(text)
        actual_ids = [number for number, _line in items]
        valid = actual_ids == list(range(1, 301)) and validate_texts([line for _number, line in items], 300)
        usage = response.usage
        details = getattr(usage, "completion_tokens_details", None)
        report.update({
            "status": "ok" if valid else "incomplete", "model_returned": response.model,
            "finish_reason": choice.finish_reason if choice else None,
            "output_cues": len(items), "valid_numbering": valid,
            "missing_ids": [number for number in range(1, 301) if number not in actual_ids],
            "usage": {
                "prompt_tokens": getattr(usage, "prompt_tokens", None),
                "completion_tokens": getattr(usage, "completion_tokens", None),
                "total_tokens": getattr(usage, "total_tokens", None),
                "reasoning_tokens": getattr(details, "reasoning_tokens", None),
            },
        })
        if valid:
            translated = [dict(segment, text=items[index][1]) for index, segment in enumerate(segments)]
            (output_dir / "translated_300.srt").write_text(to_srt(translated), encoding="utf-8")
    except Exception as exc:
        report.update({"status": "error", "error_type": type(exc).__name__,
                       "http_status": getattr(exc, "status_code", None),
                       "error": str(exc).replace(api_key, "[redacted]")})
    report["elapsed_seconds"] = round(time.monotonic() - started, 3)
    (output_dir / "response.txt").write_text(text, encoding="utf-8")
    (output_dir / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main() -> int:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    config_path = OUTPUT_DIR / "config.json"
    if not config_path.exists():
        config_path.write_text(json.dumps(DEFAULT_CONFIG, indent=2), encoding="utf-8")
    config = json.loads(config_path.read_text(encoding="utf-8-sig"))
    if not str(config.get("api_key") or "").strip():
        print(f"Fill api_key and select the Gemini model in: {config_path}")
        return 2
    print(f"Sending 300 cues to {config['model']} (one request, no automatic retry)...", flush=True)
    report = probe_gemini_300(**config)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"Report: {OUTPUT_DIR / 'report.json'}")
    return 0 if report["status"] == "ok" else 1


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
