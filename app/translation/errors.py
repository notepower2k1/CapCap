class TranslationError(Exception):
    pass


class TranslationConfigError(TranslationError):
    pass


class TranslationProviderError(TranslationError):
    pass


class TranslationValidationError(TranslationError):
    pass
