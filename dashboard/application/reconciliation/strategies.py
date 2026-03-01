"""
Per-provider reconciliation strategies.
Defines custom reconciliation behavior for providers with unique characteristics.
"""
import logging

logger = logging.getLogger(__name__)


# Provider-specific notes for reconciliation context
PROVIDER_STRATEGIES = {
    "anthropic": {
        "primary_source": "ledger",  # No direct balance API
        "api_balance_available": False,
        "api_usage_available": True,  # Can get per-request costs from JSONL
        "scrape_target": "https://console.anthropic.com/settings/billing",
        "notes": (
            "Anthropic has no balance API. Primary source is ledger deposits minus "
            "computed DB costs. Scraping the billing page is a validation-only fallback. "
            "Per-request costs come from JSONL session files (API-reported usage block)."
        ),
        "scrape_frequency_hours": 12,  # Only scrape twice a day for validation
        "confidence_without_api": 0.75,  # Ledger + computed is fairly reliable
    },
    "moonshot": {
        "primary_source": "api",  # Has balance API
        "api_balance_available": True,
        "api_usage_available": False,
        "scrape_target": "https://platform.moonshot.ai/console/account",
        "notes": (
            "Moonshot has a balance API endpoint. Use it as primary source. "
            "Multi-project support: each project has its own ledger and model list."
        ),
        "scrape_frequency_hours": 24,  # API is primary, rarely need scraping
        "confidence_without_api": 0.65,
    },
    "minimax": {
        "primary_source": "ledger",  # No known balance API
        "api_balance_available": False,
        "api_usage_available": False,
        "scrape_target": "https://www.minimaxi.com/platform",
        "notes": (
            "MiniMax has no known balance or usage API. Relies on ledger deposits "
            "minus computed costs. Scraping validates periodically."
        ),
        "scrape_frequency_hours": 6,  # More frequent since no API
        "confidence_without_api": 0.60,
    },
}


def get_strategy(provider: str) -> dict:
    """Get reconciliation strategy for a provider."""
    return PROVIDER_STRATEGIES.get(provider, {
        "primary_source": "computed",
        "api_balance_available": False,
        "api_usage_available": False,
        "scrape_target": None,
        "notes": "Unknown provider, using computed balance only",
        "scrape_frequency_hours": 6,
        "confidence_without_api": 0.50,
    })


def should_scrape(provider: str, hours_since_last_scrape: float, confidence: float) -> bool:
    """
    Determine if a scrape is needed for this provider.
    Scraping is a last resort - only when:
    1. No API is available AND
    2. Confidence has dropped below threshold AND
    3. Enough time has passed since last scrape
    """
    strategy = get_strategy(provider)

    # If API is available and working, rarely need scraping
    if strategy["api_balance_available"] and confidence >= 0.8:
        return False

    # Scrape if enough time has passed and confidence is low
    min_hours = strategy["scrape_frequency_hours"]
    if hours_since_last_scrape >= min_hours and confidence < 0.6:
        return True

    # Always scrape if it's been very long (48h+) regardless of confidence
    if hours_since_last_scrape >= 48:
        return True

    return False
