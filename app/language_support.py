"""Language choices and capabilities shared by UI and inference adapters."""

LANGUAGES = (
    ("Vietnamese", "vi"), ("English", "en"), ("Korean", "ko"),
    ("Japanese", "ja"), ("Thai", "th"), ("Russian", "ru"),
    ("French", "fr"), ("German", "de"),
)
SOURCE_LANGUAGES = (("Auto Detect", "auto"), ("Chinese", "zh")) + LANGUAGES
VOICE_PREVIEW_TEXT = {
    "vi": "Chào bạn, đây là bản xem trước giọng nói của mẫu được chọn.",
    "en": "Hello, this is a preview of the selected voice sample.",
    "ko": "안녕하세요. 선택한 목소리의 미리 듣기입니다.",
    "ja": "こんにちは。選択した音声のサンプルです。",
    "th": "สวัสดี นี่คือตัวอย่างเสียงที่คุณเลือก",
    "ru": "Здравствуйте! Это образец выбранного вами голоса.",
    "fr": "Bonjour, voici un aperçu de la voix sélectionnée.",
    "de": "Hallo, dies ist eine Vorschau der ausgewählten Stimme.",
}
SENSEVOICE_LANGUAGES = frozenset({"auto", "zh", "en", "ja", "ko", "yue"})
VOICE_ENGINES = (("Piper Voice", "piper"), ("VieNeu TTS", "vieneu"),
                 ("OmniVoice (audio.cpp)", "omnivoice"), ("CapCut API (Beta)", "capcut"), ("ElevenLabs API", "elevenlabs"))


def language_code(value):
    code = str(value or "").strip().lower().replace("_", "-").split("-")[0]
    return {"jp": "ja", "kr": "ko"}.get(code, code)


def voice_matches_language(entry, language):
    code = language_code(entry.get("language"))
    target = language_code(language)
    if entry.get("provider") == "vieneu":
        return target == "vi"
    return code == target or code in {"multilingual", "multi", "any", "all"} or (
        not code and target in {"vi", "en"}
    )


def available_voice_engines(language, voices=()):
    target = language_code(language)
    available = {"elevenlabs", "omnivoice"}  # Configuration/key availability is a separate concern.
    if target in {"vi", "en"}:
        available.add("piper")  # These packs are offered by Manage Resources.
    if target == "vi":
        available.add("vieneu")
    for entry in voices or ():
        if isinstance(entry, dict) and entry.get("enabled", True) and voice_matches_language(entry, target):
            provider = str(entry.get("provider", "")).lower()
            if provider == "edge":
                provider = "piper"
            if provider != "vieneu" or target == "vi":
                available.add(provider)
    return [(label, key) for label, key in VOICE_ENGINES if key in available]


def elevenlabs_supports_language(model_id, language):
    """Capabilities for the models exposed by CapCap, within our output choices."""
    model = str(model_id or "eleven_turbo_v2_5")
    target = language_code(language)
    if not target or target in {"auto", "multilingual", "multi", "any", "all"}:
        return True
    if model in {"eleven_monolingual_v1", "eleven_turbo_v2", "eleven_flash_v2"}:
        return target == "en"
    if target == "th":
        return model in {"eleven_v3", "eleven_v4", "eleven_v4_turbo"}
    # For other languages/custom models let the provider perform validation.
    return True
