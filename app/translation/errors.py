class TranslationError(Exception):
    pass


class TranslationConfigError(TranslationError):
    pass


class TranslationProviderError(TranslationError):
    pass


class TranslationValidationError(TranslationError):
    pass


def translation_failure_reason(error: Exception) -> str:
    message = ""
    while error is not None:
        if isinstance(error, TranslationConfigError):
            return "AI API key or model is not configured."
        if isinstance(error, TranslationValidationError):
            return "AI returned incomplete or invalid subtitles."
        status = getattr(error, "status_code", None)
        reasons = {
            401: "AI API authentication failed (HTTP 401).",
            403: "AI API access was denied (HTTP 403).",
            429: "AI API request or quota limit reached (HTTP 429).",
            503: "AI service is temporarily overloaded or unavailable (HTTP 503).",
            504: "AI service did not respond in time (HTTP 504).",
        }
        if status in reasons:
            return reasons[status]
        if isinstance(status, int) and 500 <= status < 600:
            return "AI service returned a server error."
        message = f"{type(error).__name__} {error}".lower()
        error = error.__cause__
    if "timeout" in message or "timed out" in message:
        return "AI service did not respond within the time limit."
    if "connectionerror" in message or "connecterror" in message or "connection failed" in message:
        return "Could not connect to the AI service."
    if "empty response" in message:
        return "AI returned an empty response."
    return "AI translation failed for an unknown reason."
