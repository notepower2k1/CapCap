from importlib import import_module

__all__ = [
    "AIPolisherProvider",
    "GeminiPolisherProvider",
    "GoogleWebTranslatorProvider",
    "LocalPolisherProvider",
    "MicrosoftTranslatorProvider",
]

_MODULE_MAP = {
    "AIPolisherProvider": ".ai_polisher",
    "GeminiPolisherProvider": ".gemini_polisher",
    "GoogleWebTranslatorProvider": ".google_web_translator",
    "LocalPolisherProvider": ".local_polisher",
    "MicrosoftTranslatorProvider": ".microsoft_translator",
}


def __getattr__(name):
    module_name = _MODULE_MAP.get(name)
    if not module_name:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module = import_module(module_name, __name__)
    value = getattr(module, name)
    globals()[name] = value
    return value
