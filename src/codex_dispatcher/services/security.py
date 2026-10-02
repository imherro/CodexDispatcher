import logging
import re

_PATTERNS = [
    r'\b(?:gh[pousr]_[A-Za-z0-9_]+|github_pat_[A-Za-z0-9_]+|sk-[A-Za-z0-9_-]+)\b',
    r'(?i)(?:Bearer\s+)[A-Za-z0-9._~+/=-]+',
    r'(?i)(?:access_token|refresh_token|id_token|api_key|oauth_token|client_secret|password|token)\s*[=:]\s*["\x27]?[^\s,"\x27}]+',
    r'\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b',
    r'https?://[^\s/@]+:[^\s/@]+@',
]


def redact(value) -> str:
    text = str(value)
    for pattern in _PATTERNS:
        text = re.sub(pattern, '[REDACTED]', text)
    return text


class RedactingFormatter(logging.Formatter):
    def format(self, record):
        return redact(super().format(record))
