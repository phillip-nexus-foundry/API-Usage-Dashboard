"""
Base scraper interface.
Scrapers are demoted to validation-only sources in the 3-tier architecture.
They should only run when:
1. No API balance endpoint is available for the provider
2. Confidence in computed balance has dropped below threshold
3. Enough time has passed since the last scrape
"""
import logging
from typing import Optional
from dataclasses import dataclass
from datetime import datetime

logger = logging.getLogger(__name__)


@dataclass
class ScrapeResult:
    """Result of a balance scrape from a provider console."""
    provider: str
    balance: Optional[float] = None
    currency: str = "USD"
    tier: Optional[str] = None
    error: Optional[str] = None
    raw_html: Optional[str] = None
    timestamp: Optional[datetime] = None
    confidence: float = 0.6  # Scraped data is lower confidence than API

    @property
    def success(self) -> bool:
        return self.balance is not None and self.error is None


class BaseScraper:
    """
    Abstract base for provider console scrapers.
    Uses Playwright for browser automation.

    In the 3-tier architecture, scrapers are VALIDATION sources, not primary.
    They run on a reduced schedule (every 6-12 hours) and their results
    get a lower confidence score (0.6) compared to API (0.95) or computed (0.7).
    """

    def __init__(self, provider: str, profiles_dir: str = "browser_profiles"):
        self.provider = provider
        self.profiles_dir = profiles_dir

    async def scrape(self) -> ScrapeResult:
        """
        Perform the scrape. Subclasses implement provider-specific logic.
        Returns ScrapeResult with balance or error.
        """
        raise NotImplementedError

    def should_run(self, hours_since_last: float, current_confidence: float) -> bool:
        """
        Determine if this scraper should run.
        Only scrape when confidence is low and enough time has passed.
        """
        from dashboard.application.reconciliation.strategies import should_scrape
        return should_scrape(self.provider, hours_since_last, current_confidence)
