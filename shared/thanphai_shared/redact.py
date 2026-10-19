"""Keep API keys out of logs and error messages.

FIRMS puts its MAP_KEY in the URL path, and httpx logs full URLs at INFO and includes them in exception
messages. Found 2026-10-19: the key appeared 8 times in the api-backend container log.
"""

import logging
import os
import re

SECRET_ENV = ("FIRMS_MAP_KEY", "GISTDA_API_KEY", "TMD_NWP_TOKEN", "EARTHDATA_TOKEN", "GEMINI_API_KEY",
              "GOOGLE_API_KEY", "LINE_CHANNEL_ACCESS_TOKEN", "LINE_CHANNEL_SECRET", "POSTGRES_PASSWORD")
# Query parameters that carry keys in URLs we call or receive (GISTDA echoes ?api_key=...).
KEY_PARAMS = re.compile(r"((?:api_key|apikey|key|token|ukey)=)[^&\s\"']+", re.I)


def _secrets() -> list[str]:
    # Short values (e.g. a demo "api") would redact ordinary words; real keys are long.
    return sorted({v for name in SECRET_ENV if len(v := os.getenv(name, "")) >= 12}, key=len, reverse=True)


def redact(text: str) -> str:
    for secret in _secrets():
        text = text.replace(secret, "***")
    return KEY_PARAMS.sub(r"\1***", text)


class RedactFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        cleaned = redact(message)
        if cleaned != message:
            record.msg, record.args = cleaned, None
        return True


def install() -> None:
    """Redact every handler on the root logger, and keep httpx from logging each request URL."""
    root = logging.getLogger()
    for handler in root.handlers or []:
        handler.addFilter(RedactFilter())
    for name in ("httpx", "httpcore"):
        logging.getLogger(name).setLevel(logging.WARNING)
    # uvicorn handlers are configured separately.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        for handler in logging.getLogger(name).handlers:
            handler.addFilter(RedactFilter())
