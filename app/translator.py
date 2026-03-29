import os

from dotenv import load_dotenv

from translation import TranslationOrchestrator
from translation.errors import TranslationError
from translation.srt_utils import to_srt


base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
env_path = os.path.join(base_dir, ".env")
load_dotenv(env_path)


def translate_segments_to_srt(srt_text, model_path=None, src_lang="auto", enable_polish=True):
    orchestrator = TranslationOrchestrator()
    result = orchestrator.translate_srt(
        srt_text,
        src_lang=src_lang,
        target_lang="vi",
        enable_polish=enable_polish,
    )
    if not result.success:
        raise TranslationError("; ".join(result.errors) or "Translation failed.")
    return orchestrator.result_to_srt(result)


def translate_segments(segments, model_path=None, src_lang="auto", enable_polish=True):
    orchestrator = TranslationOrchestrator()
    result = orchestrator.translate_segments(
        segments,
        src_lang=src_lang,
        target_lang="vi",
        enable_polish=enable_polish,
    )
    if not result.success:
        raise TranslationError("; ".join(result.errors) or "Translation failed.")
    return result.segments


def rewrite_translated_segments(source_segments, translated_segments, model_path=None, src_lang="auto", style_instruction=""):
    orchestrator = TranslationOrchestrator()
    result = orchestrator.rewrite_segments(
        source_segments,
        translated_segments,
        src_lang=src_lang,
        target_lang="vi",
        style_instruction=style_instruction,
    )
    if not result.success:
        raise TranslationError("; ".join(result.errors) or "Rewrite failed.")
    return result.segments


def rewrite_translated_segments_to_srt(source_segments, translated_segments, model_path=None, src_lang="auto", style_instruction=""):
    segments = rewrite_translated_segments(
        source_segments,
        translated_segments,
        model_path=model_path,
        src_lang=src_lang,
        style_instruction=style_instruction,
    )
    return to_srt(segments)
