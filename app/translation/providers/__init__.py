from importlib import import_module

__all__ = [
    "OpenAICompatiblePolisherProvider",
    "GoogleWebTranslatorProvider",
    "BingWebTranslatorProvider",
]

_MODULE_MAP = {
    "OpenAICompatiblePolisherProvider": ".gemini_polisher",
    "GoogleWebTranslatorProvider": ".google_web_translator",
    "BingWebTranslatorProvider": ".bing_web_translator",
}


def __getattr__(name):
    module_name = _MODULE_MAP.get(name)
    if not module_name:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module = import_module(module_name, __name__)
    value = getattr(module, name)
    globals()[name] = value
    return value
