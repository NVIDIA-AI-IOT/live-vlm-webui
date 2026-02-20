"""
Webhook configuration loading with backward-compatible defaults.
"""

from dataclasses import dataclass
import logging
import os

logger = logging.getLogger(__name__)


@dataclass
class WebhookConfig:
    enabled: bool = False
    url: str = ""
    timeout_sec: float = 2.0
    mode: str = "both"  # single|multi|both
    sample_every: int = 1
    include_metrics: bool = True


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


def load_webhook_config() -> WebhookConfig:
    """
    Load webhook settings from environment variables.
    Webhook is disabled by default to preserve existing behavior.
    """
    enabled = _env_bool("LIVE_VLM_WEBHOOK_ENABLED", False)
    url = os.getenv("LIVE_VLM_WEBHOOK_URL", "").strip()
    timeout_raw = os.getenv("LIVE_VLM_WEBHOOK_TIMEOUT_SEC", "2.0")
    mode = os.getenv("LIVE_VLM_WEBHOOK_MODE", "both").strip().lower() or "both"
    sample_raw = os.getenv("LIVE_VLM_WEBHOOK_SAMPLE_EVERY", "1")
    include_metrics = _env_bool("LIVE_VLM_WEBHOOK_INCLUDE_METRICS", True)

    try:
        timeout_sec = float(timeout_raw)
        if timeout_sec <= 0:
            raise ValueError
    except ValueError:
        logger.warning(
            "Invalid LIVE_VLM_WEBHOOK_TIMEOUT_SEC=%s, using default 2.0",
            timeout_raw,
        )
        timeout_sec = 2.0

    try:
        sample_every = int(sample_raw)
        if sample_every < 1:
            raise ValueError
    except ValueError:
        logger.warning(
            "Invalid LIVE_VLM_WEBHOOK_SAMPLE_EVERY=%s, using default 1",
            sample_raw,
        )
        sample_every = 1

    if mode not in {"single", "multi", "both"}:
        logger.warning("Invalid LIVE_VLM_WEBHOOK_MODE=%s, using default 'both'", mode)
        mode = "both"

    # If enabled but URL is missing, disable safely to preserve runtime behavior.
    if enabled and not url:
        logger.warning("LIVE_VLM_WEBHOOK_ENABLED is true but LIVE_VLM_WEBHOOK_URL is empty")
        enabled = False

    return WebhookConfig(
        enabled=enabled,
        url=url,
        timeout_sec=timeout_sec,
        mode=mode,
        sample_every=sample_every,
        include_metrics=include_metrics,
    )
