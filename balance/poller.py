"""
Background balance polling and drift calibration.
"""
import asyncio
import json
import os
import logging
import re
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx
import yaml

logger = logging.getLogger(__name__)

# Brave CDP port for connecting to running instance
_BRAVE_CDP_PORT = int(os.environ.get("BRAVE_CDP_PORT", "9222"))

_PROVIDER_ALIASES = {
    "minimax": ["minimax", "mini_max", "minimaxai"],
    "codex_cli": ["codex_cli", "openclaw"],
}


class BalancePoller:
    """Poll provider balances and persist snapshots for drift monitoring."""

    def __init__(
        self,
        config: Dict[str, Any],
        db_path: str = "dashboard.db",
        profiles_dir: str = "browser_profiles",
        config_path: Optional[str] = None,
        alert_threshold_pct: float = 5.0,
        autocorrect_threshold_pct: float = 10.0,
        auto_correct: bool = False,
    ):
        self.config = config
        self.db_path = db_path
        self.profiles_dir = Path(profiles_dir)
        self.profiles_dir.mkdir(parents=True, exist_ok=True)
        self.config_path = config_path
        self.alert_threshold_pct = alert_threshold_pct
        self.autocorrect_threshold_pct = autocorrect_threshold_pct
        self.auto_correct = auto_correct

    def refresh_config(self, config: Dict[str, Any]):
        self.config = config

    async def poll_all(self, providers: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        provider_order = providers or ["anthropic", "elevenlabs", "codex_cli", "moonshot", "minimax"]
        results = []
        for provider in provider_order:
            poller = getattr(self, f"poll_{provider}", None)
            if not poller:
                continue
            try:
                snapshot = await poller()
            except Exception as exc:
                logger.error("Polling failed for %s: %s", provider, exc)
                snapshot = self._error_snapshot(provider, str(exc))
            self._insert_snapshot(snapshot)
            results.append(snapshot)
        return results

    async def poll_moonshot(self) -> Dict[str, Any]:
        """Moonshot balance is tracked by the balance checker API — skip browser scraping."""
        return self._build_snapshot(
            provider="moonshot",
            snapshot_type="usage_status",
            balance_amount=0.0,
            balance_currency="USD",
            balance_source="api_only",
            raw_response={"note": "Moonshot uses balance checker API, not browser scraping."},
        )

    async def poll_anthropic(self) -> Dict[str, Any]:
        snapshot = await self._poll_provider_page(
            provider="anthropic",
            url="https://console.anthropic.com/settings/billing",
            selectors=[
                "[data-testid='balance-display']",
                "[data-testid='credits-balance']",
                "text=/\\$\\s*[0-9,.]+/",
            ],
        )
        # Enrich with Claude UI usage data (spend limit + extra usage balance) if available.
        usage = await self._poll_claude_usage_page()
        if usage:
            raw = snapshot.get("raw_response") or {}
            raw["claude_usage"] = usage
            snapshot["raw_response"] = raw
        return snapshot

    async def poll_minimax(self) -> Dict[str, Any]:
        return await self._poll_provider_page(
            provider="minimax",
            url="https://platform.minimax.io/user-center/payment/balance",
            selectors=[
                "text=/[￥¥$]\\s*[0-9,.]+/",
                ".balance",
                "[class*='balance']",
            ],
        )

    async def poll_elevenlabs(self) -> Dict[str, Any]:
        """Poll ElevenLabs subscription info. Prefers REST API when
        XI_API_KEY or ELEVENLABS_API_KEY is set; falls back to browser scraping."""

        # --- Try API first (far more reliable than browser scraping) ---
        api_key = os.environ.get("XI_API_KEY") or os.environ.get("ELEVENLABS_API_KEY")
        if api_key:
            try:
                async with httpx.AsyncClient() as client:
                    resp = await client.get(
                        "https://api.elevenlabs.io/v1/user/subscription",
                        headers={"xi-api-key": api_key},
                        timeout=10.0,
                    )
                    resp.raise_for_status()
                    data = resp.json()
                remaining = data.get("character_count", 0)
                total = data.get("character_limit", 0)
                plan_name = data.get("tier", "").replace("_", " ").title() or None
                return self._build_snapshot(
                    provider="elevenlabs",
                    snapshot_type="full_status",
                    balance_amount=float(remaining),
                    balance_currency="credits",
                    balance_source="api",
                    tier=plan_name,
                    total_credits=float(total) if total else None,
                    raw_response={"source": "api", "character_count": remaining, "character_limit": total},
                )
            except Exception as exc:
                logger.warning("ElevenLabs API check failed, falling back to browser: %s", exc)

        # --- Browser scraping fallback ---
        try:
            from playwright.async_api import async_playwright
        except Exception:
            return self._error_snapshot("elevenlabs", "playwright not installed")

        url = "https://elevenlabs.io/app/subscription"
        raw_response: Dict[str, Any] = {"url": url}
        page_text = ""
        try:
            async with async_playwright() as p:
                profile_dir = str(self.profiles_dir / "elevenlabs")
                context = await p.chromium.launch_persistent_context(
                    user_data_dir=profile_dir,
                    headless=True,
                    viewport={"width": 1400, "height": 900},
                )
                page = await context.new_page()
                await page.goto(url, wait_until="domcontentloaded", timeout=45000)
                try:
                    await page.wait_for_load_state("networkidle", timeout=20000)
                except Exception:
                    pass
                # Wait for credits text to appear in the DOM
                try:
                    await page.wait_for_function(
                        "() => document.body && /credit/i.test(document.body.innerText || '')",
                        timeout=20000,
                    )
                except Exception:
                    pass
                await page.wait_for_timeout(3000)
                page_text = await self._read_body_text(page, context)
                raw_response["body_length"] = len(page_text or "")
                raw_response["body_preview"] = (page_text or "")[:500]

                # Try authenticated API call via browser session cookies.
                try:
                    sub_resp = await context.request.get(
                        "https://api.elevenlabs.io/v1/user/subscription",
                        timeout=10000,
                    )
                    if sub_resp.status == 200:
                        data = await sub_resp.json()
                        remaining = data.get("character_count", 0)
                        total = data.get("character_limit", 0)
                        plan_name = data.get("tier", "").replace("_", " ").title() or None
                        await context.close()
                        return self._build_snapshot(
                            provider="elevenlabs",
                            snapshot_type="full_status",
                            balance_amount=float(remaining),
                            balance_currency="credits",
                            balance_source="browser_session_api",
                            tier=plan_name,
                            total_credits=float(total) if total else None,
                            raw_response={"source": "browser_session_api", "character_count": remaining, "character_limit": total},
                        )
                except Exception:
                    pass

                await context.close()
        except Exception as exc:
            return self._error_snapshot(
                "elevenlabs",
                f"Browser scrape failed: {exc}",
                raw_response=raw_response,
            )

        # Detect login redirect (only if page is short / clearly a login form)
        if len(page_text or "") < 2000:
            lower_start = (page_text or "").lower()
            if "sign in" in lower_start or "log in" in lower_start or "create account" in lower_start:
                return self._error_snapshot(
                    "elevenlabs",
                    "Not logged in to ElevenLabs browser profile.",
                    raw_response=raw_response,
                )

        parsed = self._parse_elevenlabs_subscription_text(page_text or "")
        if parsed["remaining_credits"] is None:
            return self._error_snapshot(
                "elevenlabs",
                "Credits not found on ElevenLabs subscription page.",
                raw_response=raw_response,
            )

        return self._build_snapshot(
            provider="elevenlabs",
            snapshot_type="full_status",
            balance_amount=parsed["remaining_credits"],
            balance_currency="credits",
            balance_source="browser_poll",
            tier=parsed["plan_name"],
            total_credits=parsed["total_credits"],
            raw_response=raw_response,
        )

    async def poll_codex_cli(self) -> Dict[str, Any]:
        """
        Scrape Codex CLI usage from chatgpt.com/codex/settings/usage via CDP.
        Uses sync Playwright in a thread to avoid event loop conflicts with uvicorn.
        Falls back to synthetic if CDP is unavailable or the page isn't open.
        """
        use_cdp = bool(self.config.get("use_brave_cdp", False))
        if not use_cdp:
            return self._build_snapshot(
                provider="codex_cli",
                snapshot_type="usage_status",
                balance_amount=0.0,
                balance_currency="credits",
                balance_source="synthetic",
                raw_response={"note": "No direct Codex CLI endpoint; enable use_brave_cdp and open the usage page."},
            )

        codex_data = {}
        try:
            codex_data = await asyncio.to_thread(self._cdp_scrape_codex_sync)
            if codex_data.get("error"):
                logger.info("CDP Codex scrape: %s", codex_data["error"])
                codex_data = {}
        except Exception as e:
            logger.warning("Codex CLI CDP scrape thread failed: %s", e)

        raw = {
            "codex_usage": codex_data,
            "source": "cdp_scrape",
        }
        return self._build_snapshot(
            provider="codex_cli",
            snapshot_type="usage_status",
            balance_amount=0.0,
            balance_currency="credits",
            balance_source="cdp_scrape" if codex_data else "error",
            raw_response=raw,
        )

    @staticmethod
    def _find_tab(browser, url_fragment: str):
        """Find an existing tab whose URL contains the given fragment."""
        for ctx in browser.contexts:
            for page in ctx.pages:
                if url_fragment in (page.url or ""):
                    return page
        return None

    @staticmethod
    def _cdp_scrape_claude_sync() -> Dict[str, Any]:
        """Sync CDP scrape for Claude usage page. Runs in a thread to avoid
        event loop conflicts with uvicorn."""
        try:
            from playwright.sync_api import sync_playwright
        except Exception:
            return {}

        try:
            with sync_playwright() as p:
                browser = p.chromium.connect_over_cdp(
                    f"http://127.0.0.1:{_BRAVE_CDP_PORT}", timeout=10000,
                )
                page = None
                for ctx in browser.contexts:
                    for pg in ctx.pages:
                        if "claude.ai/settings/usage" in (pg.url or ""):
                            page = pg
                            break
                    if page:
                        break

                if not page:
                    browser.close()
                    return {"error": "Claude usage tab not open in Brave."}

                # Read page as-is — do NOT reload, which can cause login redirects
                # and conflicts with other CDP connections.

                result = page.evaluate(r'''() => {
                    const body = document.body.innerText || "";
                    const lines = body.split("\n").map(l => l.trim()).filter(Boolean);
                    const result = {
                        plan_usage_pct: null,
                        plan_usage_reset: null,
                        weekly_pct: null,
                        weekly_reset: null,
                        extra_usage_pct: null,
                        extra_usage_reset: null,
                        spend_used: null,
                        spend_limit: null,
                    };
                    for (let i = 0; i < lines.length; i++) {
                        const line = lines[i].toLowerCase();
                        if (line.includes("plan usage limit")) {
                            for (let j = i + 1; j < Math.min(i + 5, lines.length); j++) {
                                const l = lines[j].trim();
                                if (/resets?\s+/i.test(l) && result.plan_usage_reset === null) result.plan_usage_reset = l;
                                const m = l.match(/^(\d+)%\s*used/i);
                                if (m && result.plan_usage_pct === null) result.plan_usage_pct = parseInt(m[1], 10);
                            }
                        }
                        if (line === "weekly limits" || line === "weekly limit") {
                            for (let j = i + 1; j < Math.min(i + 5, lines.length); j++) {
                                const l = lines[j].trim();
                                if (/resets?\s+/i.test(l) && result.weekly_reset === null) result.weekly_reset = l;
                                const m = l.match(/^(\d+)%\s*used/i);
                                if (m && result.weekly_pct === null) result.weekly_pct = parseInt(m[1], 10);
                            }
                        }
                        if (line.startsWith("extra usage")) {
                            for (let j = i + 1; j < Math.min(i + 5, lines.length); j++) {
                                const l = lines[j].trim();
                                if (/resets?\s+/i.test(l) && result.extra_usage_reset === null) result.extra_usage_reset = l;
                                const m = l.match(/^(\d+)%\s*used/i);
                                if (m && result.extra_usage_pct === null) result.extra_usage_pct = parseInt(m[1], 10);
                            }
                        }
                        // "$X / $Y" format (old layout)
                        const spendMatch = line.match(/\$\s*([\d.]+)\s*\/\s*\$\s*([\d.]+)/);
                        if (spendMatch) {
                            result.spend_used = parseFloat(spendMatch[1]);
                            result.spend_limit = parseFloat(spendMatch[2]);
                        }
                        // "$X spent" format (current layout)
                        const spentMatch = line.match(/\$\s*([\d.]+)\s*spent/i);
                        if (spentMatch && result.spend_used === null) {
                            result.spend_used = parseFloat(spentMatch[1]);
                        }
                        // "Monthly spend limit" preceded by "$X" on prev line
                        if (line.includes("monthly spend limit") && i > 0) {
                            const prevLine = lines[i - 1].trim();
                            const limMatch = prevLine.match(/^\$\s*([\d.]+)$/);
                            if (limMatch && result.spend_limit === null) {
                                result.spend_limit = parseFloat(limMatch[1]);
                            }
                        }
                    }
                    return result;
                }''')

                browser.close()
                return result
        except Exception as e:
            logger.warning("CDP sync scrape (Claude) failed: %s", e)
            return {}

    @staticmethod
    def _cdp_scrape_codex_sync() -> Dict[str, Any]:
        """Sync CDP scrape for Codex usage page. Runs in a thread to avoid
        event loop conflicts with uvicorn."""
        try:
            from playwright.sync_api import sync_playwright
        except Exception:
            return {}

        try:
            with sync_playwright() as p:
                browser = p.chromium.connect_over_cdp(
                    f"http://127.0.0.1:{_BRAVE_CDP_PORT}", timeout=10000,
                )
                page = None
                for ctx in browser.contexts:
                    for pg in ctx.pages:
                        if "chatgpt.com/codex/settings/usage" in (pg.url or ""):
                            page = pg
                            break
                    if page:
                        break

                if not page:
                    browser.close()
                    return {"error": "Codex usage tab not open in Brave."}

                # Read page as-is — do NOT reload.

                result = page.evaluate(r'''() => {
                    const body = document.body.innerText || "";
                    const lines = body.split("\n").map(l => l.trim()).filter(Boolean);
                    const result = {
                        five_hour_remaining_pct: null,
                        weekly_remaining_pct: null,
                        weekly_reset: null,
                    };
                    for (let i = 0; i < lines.length; i++) {
                        const line = lines[i].toLowerCase();
                        if (line.includes("5 hour usage limit") && !line.includes("spark")) {
                            for (let j = i + 1; j < Math.min(i + 4, lines.length); j++) {
                                const m = lines[j].trim().match(/^(\d+)%$/);
                                if (m && result.five_hour_remaining_pct === null) result.five_hour_remaining_pct = parseInt(m[1], 10);
                            }
                        }
                        if (line.includes("weekly usage limit") && !line.includes("spark")) {
                            for (let j = i + 1; j < Math.min(i + 4, lines.length); j++) {
                                const l = lines[j].trim();
                                const m = l.match(/^(\d+)%$/);
                                if (m && result.weekly_remaining_pct === null) result.weekly_remaining_pct = parseInt(m[1], 10);
                                if (/resets?\s+/i.test(l) && result.weekly_reset === null) result.weekly_reset = l;
                            }
                        }
                        if (/^resets?\s+/i.test(lines[i]) && result.weekly_reset === null) result.weekly_reset = lines[i].trim();
                    }
                    return result;
                }''')

                browser.close()
                return result
        except Exception as e:
            logger.warning("CDP sync scrape (Codex) failed: %s", e)
            return {}

    async def _extract_claude_usage(self, page) -> Dict[str, Any]:
        """Extract Claude Code usage data from the claude.ai/settings/usage page DOM."""
        return await page.evaluate(r'''() => {
            const body = document.body.innerText || "";
            const lines = body.split("\n").map(l => l.trim()).filter(Boolean);
            const result = {
                plan_usage_pct: null,
                plan_usage_reset: null,
                weekly_pct: null,
                weekly_reset: null,
                extra_usage_pct: null,
                extra_usage_reset: null,
                spend_used: null,
                spend_limit: null,
            };

            // Find sections by scanning lines for known labels
            for (let i = 0; i < lines.length; i++) {
                const line = lines[i].toLowerCase();
                const next = (lines[i + 1] || "").trim();
                const next2 = (lines[i + 2] || "").trim();

                // "Plan usage limits" section — next lines have reset info + percentage
                if (line.includes("plan usage limit")) {
                    // Scan ahead for "Resets in ..." and "X% used"
                    for (let j = i + 1; j < Math.min(i + 5, lines.length); j++) {
                        const l = lines[j].trim();
                        if (/resets?\s+/i.test(l) && result.plan_usage_reset === null) {
                            result.plan_usage_reset = l;
                        }
                        const pctMatch = l.match(/^(\d+)%\s*used/i);
                        if (pctMatch && result.plan_usage_pct === null) {
                            result.plan_usage_pct = parseInt(pctMatch[1], 10);
                        }
                    }
                }

                // "Weekly limits" section
                if (line === "weekly limits" || line === "weekly limit") {
                    for (let j = i + 1; j < Math.min(i + 5, lines.length); j++) {
                        const l = lines[j].trim();
                        if (/resets?\s+/i.test(l) && result.weekly_reset === null) {
                            result.weekly_reset = l;
                        }
                        const pctMatch = l.match(/^(\d+)%\s*used/i);
                        if (pctMatch && result.weekly_pct === null) {
                            result.weekly_pct = parseInt(pctMatch[1], 10);
                        }
                    }
                }

                // "Extra usage" section
                if (line.startsWith("extra usage")) {
                    for (let j = i + 1; j < Math.min(i + 5, lines.length); j++) {
                        const l = lines[j].trim();
                        if (/resets?\s+/i.test(l) && result.extra_usage_reset === null) {
                            result.extra_usage_reset = l;
                        }
                        const pctMatch = l.match(/^(\d+)%\s*used/i);
                        if (pctMatch && result.extra_usage_pct === null) {
                            result.extra_usage_pct = parseInt(pctMatch[1], 10);
                        }
                    }
                }

                // "$X / $Y" spend pattern (monthly spend limit section)
                const spendMatch = line.match(/\$\s*([\d.]+)\s*\/\s*\$\s*([\d.]+)/);
                if (spendMatch) {
                    result.spend_used = parseFloat(spendMatch[1]);
                    result.spend_limit = parseFloat(spendMatch[2]);
                }
            }

            // Also read progress bar widths as fallback/validation
            const bars = [];
            document.querySelectorAll('div[style*="width"]').forEach(el => {
                const w = el.style.width;
                if (w && w.includes("%") && el.className.includes("transition")) {
                    bars.push(parseFloat(w));
                }
            });
            result.progress_bars = bars;

            return result;
        }''')

    async def _extract_codex_usage(self, page) -> Dict[str, Any]:
        """Extract Codex CLI usage data from the chatgpt.com/codex/settings/usage page DOM."""
        return await page.evaluate(r'''() => {
            const body = document.body.innerText || "";
            const lines = body.split("\n").map(l => l.trim()).filter(Boolean);
            const result = {
                five_hour_remaining_pct: null,
                weekly_remaining_pct: null,
                weekly_reset: null,
            };

            for (let i = 0; i < lines.length; i++) {
                const line = lines[i].toLowerCase();

                // "5 hour usage limit" — next line is "X%" then "remaining"
                if (line.includes("5 hour usage limit") && !line.includes("spark")) {
                    for (let j = i + 1; j < Math.min(i + 4, lines.length); j++) {
                        const l = lines[j].trim();
                        const pctMatch = l.match(/^(\d+)%$/);
                        if (pctMatch && result.five_hour_remaining_pct === null) {
                            result.five_hour_remaining_pct = parseInt(pctMatch[1], 10);
                        }
                    }
                }

                // "Weekly usage limit" — next line is "X%" then "remaining"
                if (line.includes("weekly usage limit") && !line.includes("spark")) {
                    for (let j = i + 1; j < Math.min(i + 4, lines.length); j++) {
                        const l = lines[j].trim();
                        const pctMatch = l.match(/^(\d+)%$/);
                        if (pctMatch && result.weekly_remaining_pct === null) {
                            result.weekly_remaining_pct = parseInt(pctMatch[1], 10);
                        }
                        if (/resets?\s+/i.test(l) && result.weekly_reset === null) {
                            result.weekly_reset = l;
                        }
                    }
                }

                // Reset text can also be after the percentages
                if (/^resets?\s+/i.test(lines[i]) && result.weekly_reset === null) {
                    result.weekly_reset = lines[i].trim();
                }
            }

            // Read progress bar widths
            const bars = [];
            document.querySelectorAll('div[style*="width"]').forEach(el => {
                const w = el.style.width;
                if (w && w.includes("%") && el.className.includes("transition")) {
                    bars.push(parseFloat(w));
                }
            });
            result.progress_bars = bars;

            return result;
        }''')

    async def _poll_claude_usage_page(self) -> Dict[str, Any]:
        """
        Scrape Claude usage page for spend, extra usage, and usage window percentages.
        Uses sync Playwright in a thread to avoid event loop conflicts with uvicorn.
        Falls back to headless browser if CDP is unavailable.
        """
        use_cdp = bool(self.config.get("use_brave_cdp", False))

        # --- CDP path: run sync Playwright in a thread ---
        if use_cdp:
            try:
                result = await asyncio.to_thread(self._cdp_scrape_claude_sync)
                if result and not result.get("error"):
                    return {
                        "logged_in": True,
                        "spend_used": result.get("spend_used"),
                        "spend_limit": result.get("spend_limit"),
                        "spend_reset_text": result.get("extra_usage_reset"),
                        "extra_usage_balance": None,
                        "plan_usage_pct": result.get("plan_usage_pct"),
                        "plan_usage_reset": result.get("plan_usage_reset"),
                        "weekly_pct": result.get("weekly_pct"),
                        "weekly_reset": result.get("weekly_reset"),
                        "extra_usage_pct": result.get("extra_usage_pct"),
                    }
                elif result.get("error"):
                    logger.info("CDP Claude scrape: %s", result["error"])
            except Exception as e:
                logger.warning("CDP Claude scrape thread failed: %s", e)

        # No headless fallback — when use_brave_cdp is enabled, only use CDP
        # to avoid launching browser instances that leak tabs and consume memory.
        return {}

    @staticmethod
    def _cdp_scrape_provider_sync(url: str, selectors: List[str]) -> Dict[str, Any]:
        """Sync CDP scrape for generic provider pages. Runs in a thread."""
        try:
            from playwright.sync_api import sync_playwright
        except Exception:
            return {"error": "playwright not installed"}

        try:
            with sync_playwright() as p:
                browser = p.chromium.connect_over_cdp(
                    f"http://127.0.0.1:{_BRAVE_CDP_PORT}", timeout=10000,
                )

                # Find existing tab by URL instead of opening a new one.
                # Match on the URL's host+path to handle query params.
                from urllib.parse import urlparse
                target_host = urlparse(url).netloc
                page = None
                for ctx in browser.contexts:
                    for pg in ctx.pages:
                        if target_host in (pg.url or ""):
                            page = pg
                            break
                    if page:
                        break

                if not page:
                    browser.close()
                    return {"error": f"Tab not open in Brave for {url}"}

                # Read page as-is — do NOT reload.

                selected_text = None
                for selector in selectors:
                    try:
                        page.wait_for_selector(selector, timeout=6000)
                        selected_text = page.locator(selector).first.text_content() or ""
                        if selected_text:
                            break
                    except Exception:
                        continue

                page_text = ""
                try:
                    if not page.is_closed():
                        page_text = page.inner_text("body")
                except Exception:
                    pass

                final_url = page.url if not page.is_closed() else url
                browser.close()

                return {
                    "selected_text": selected_text,
                    "page_text": page_text,
                    "final_url": final_url,
                    "body_length": len(page_text),
                    "body_preview": page_text[:600],
                }
        except Exception as e:
            return {"error": str(e)}

    async def _poll_provider_page(
        self,
        provider: str,
        url: str,
        selectors: List[str],
    ) -> Dict[str, Any]:
        try:
            from playwright.async_api import async_playwright  # pylint: disable=import-outside-toplevel
        except Exception:
            return self._error_snapshot(provider, "playwright not installed")

        raw_response: Dict[str, Any] = {"url": url, "selected_text": None}
        page_text = ""
        use_cdp = bool(self.config.get("use_brave_cdp", False))

        # --- CDP path: run sync Playwright in a thread ---
        if use_cdp:
            try:
                cdp_result = await asyncio.to_thread(
                    self._cdp_scrape_provider_sync, url, selectors
                )
                if not cdp_result.get("error"):
                    raw_response["selected_text"] = cdp_result.get("selected_text")
                    raw_response["final_url"] = cdp_result.get("final_url", url)
                    raw_response["body_length"] = cdp_result.get("body_length", 0)
                    raw_response["body_preview"] = cdp_result.get("body_preview", "")
                    page_text = cdp_result.get("page_text", "")
                    # Skip to parsing below
                else:
                    logger.warning("CDP provider scrape failed for %s: %s", provider, cdp_result["error"])
                    return self._error_snapshot(provider, f"CDP scrape failed: {cdp_result['error']}", raw_response=raw_response)
            except Exception as cdp_exc:
                logger.warning("CDP provider scrape thread failed for %s: %s", provider, cdp_exc)
                return self._error_snapshot(provider, f"CDP connection failed: {cdp_exc}", raw_response=raw_response)

        # No headless fallback — CDP-only when use_brave_cdp is enabled
        # to avoid launching browser instances that leak tabs and consume memory.

        candidate_text = raw_response.get("selected_text") or page_text
        parsed = self._parse_provider_balance(provider, candidate_text or "", page_text or "")
        if parsed["amount"] is None:
            # Check if we landed on a login page (short body with auth keywords)
            lower_page = (page_text or "").lower()
            final_url = str(raw_response.get("final_url") or "").lower()
            is_login_page = (
                any(part in final_url for part in ["login", "log-in", "sign-in", "signin", "auth"])
                or ("password" in lower_page and ("email" in lower_page or "sign in" in lower_page or "log in" in lower_page))
            )
            if is_login_page:
                return self._error_snapshot(
                    provider,
                    f"Not logged in to {provider}.",
                    raw_response=raw_response,
                )
            return self._error_snapshot(
                provider,
                f"Balance not found on {provider} page. The page loaded but no balance value was detected.",
                raw_response=raw_response,
            )

        rpm_limit = self._parse_first_int(page_text, r"\bRPM[^0-9]{0,15}([0-9,]{2,})")
        rpm_used = self._parse_first_int(page_text, r"\bused[^0-9]{0,15}([0-9,]{1,})\s*/\s*[0-9,]{1,}")
        rpm_remaining = rpm_limit - rpm_used if rpm_limit is not None and rpm_used is not None else None
        tier = self._parse_first_match(page_text, r"\bTier\s*([0-9A-Za-z._-]+)")

        return self._build_snapshot(
            provider=provider,
            snapshot_type="full_status",
            balance_amount=parsed["amount"],
            balance_currency=parsed["currency"],
            balance_source="browser_poll",
            tier=tier,
            rpm_limit=rpm_limit,
            rpm_used=rpm_used,
            rpm_remaining=rpm_remaining,
            raw_response=raw_response,
        )

    @staticmethod
    async def _read_body_text(page, context) -> str:
        """Safely read body text even if the original page closed during SPA nav."""
        try:
            if page and (not page.is_closed()):
                return await page.inner_text("body")
        except Exception:
            pass
        try:
            for candidate in reversed(context.pages):
                if candidate and (not candidate.is_closed()):
                    return await candidate.inner_text("body")
        except Exception:
            pass
        return ""

    def _parse_provider_balance(self, provider: str, selected_text: str, page_text: str) -> Dict[str, Optional[float]]:
        if provider == "anthropic":
            patterns = [
                r"remaining[^$¥€£]{0,30}([$¥€£]\s*[0-9][0-9,]*(?:\.[0-9]+)?)",
                r"available[^$¥€£]{0,30}([$¥€£]\s*[0-9][0-9,]*(?:\.[0-9]+)?)",
                r"balance[^$¥€£]{0,30}([$¥€£]\s*[0-9][0-9,]*(?:\.[0-9]+)?)",
            ]
            for pattern in patterns:
                match = re.search(pattern, page_text, flags=re.IGNORECASE)
                if match:
                    parsed = self._parse_balance_text(match.group(1))
                    if parsed["amount"] is not None:
                        return parsed

        if provider in {"moonshot", "minimax"}:
            patterns = [
                r"(?:balance|available|wallet|credit)[^$¥€£]{0,30}([$¥€£]\s*[0-9][0-9,]*(?:\.[0-9]+)?)",
                r"([$¥€£]\s*[0-9][0-9,]*(?:\.[0-9]+)?)\s*(?:remaining|balance|available)",
            ]
            for pattern in patterns:
                match = re.search(pattern, page_text, flags=re.IGNORECASE)
                if match:
                    parsed = self._parse_balance_text(match.group(1))
                    if parsed["amount"] is not None:
                        return parsed

        parsed = self._parse_balance_text(selected_text or "")
        if parsed["amount"] is not None:
            return parsed
        return self._parse_balance_text(page_text or "")

    def _build_snapshot(
        self,
        provider: str,
        snapshot_type: str,
        balance_amount: Optional[float],
        balance_currency: Optional[str],
        balance_source: str,
        raw_response: Optional[Dict[str, Any]] = None,
        tier: Optional[str] = None,
        total_credits: Optional[float] = None,
        rpm_limit: Optional[int] = None,
        rpm_used: Optional[int] = None,
        rpm_remaining: Optional[int] = None,
    ) -> Dict[str, Any]:
        calibration = self._calibrate(provider, balance_amount)
        return {
            "provider": provider,
            "snapshot_type": snapshot_type,
            "timestamp": int(time.time() * 1000),
            "balance_amount": balance_amount,
            "balance_currency": balance_currency,
            "balance_source": balance_source,
            "tier": tier,
            "total_credits": total_credits,
            "rpm_limit": rpm_limit,
            "rpm_used": rpm_used,
            "rpm_remaining": rpm_remaining,
            "computed_cost": calibration.get("computed_cost"),
            "drift_amount": calibration.get("drift_amount"),
            "drift_percentage": calibration.get("drift_percentage"),
            "calibration_action": calibration.get("action"),
            "calibration_status": calibration.get("status"),
            "raw_response": raw_response or {},
            "error": None,
        }

    def _error_snapshot(
        self,
        provider: str,
        message: str,
        raw_response: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        return {
            "provider": provider,
            "snapshot_type": "full_status",
            "timestamp": int(time.time() * 1000),
            "balance_amount": None,
            "balance_currency": None,
            "balance_source": "error",
            "tier": None,
            "total_credits": None,
            "rpm_limit": None,
            "rpm_used": None,
            "rpm_remaining": None,
            "computed_cost": None,
            "drift_amount": None,
            "drift_percentage": None,
            "calibration_action": "none",
            "calibration_status": "error",
            "raw_response": raw_response or {},
            "error": message,
        }

    def _calibrate(self, provider: str, polled_balance: Optional[float]) -> Dict[str, Any]:
        if polled_balance is None:
            return {"status": "error", "action": "none"}

        computed_cost = self._computed_provider_cost(provider)
        total_deposits = self._provider_deposits(provider)
        if total_deposits is None:
            return {"computed_cost": computed_cost, "status": "no_ledger", "action": "none"}

        computed_balance = total_deposits - computed_cost
        drift_amount = float(polled_balance - computed_balance)

        if abs(computed_balance) < 1e-9:
            drift_pct = 100.0 if abs(drift_amount) > 0.01 else 0.0
        else:
            drift_pct = abs(drift_amount / computed_balance) * 100.0

        status = "ok"
        action = "none"
        if drift_pct >= self.autocorrect_threshold_pct:
            status = "critical"
            if self.auto_correct:
                corrected_cost = max(0.0, total_deposits - polled_balance)
                if self._apply_verified_usage_cost(provider, corrected_cost):
                    action = "auto_corrected"
                else:
                    action = "auto_correct_failed"
            else:
                action = "alert"
        elif drift_pct >= self.alert_threshold_pct:
            status = "warn"
            action = "alert"

        return {
            "computed_cost": round(computed_cost, 6),
            "computed_balance": round(computed_balance, 6),
            "drift_amount": round(drift_amount, 6),
            "drift_percentage": round(drift_pct, 2),
            "status": status,
            "action": action,
        }

    def _apply_verified_usage_cost(self, provider: str, corrected_cost: float) -> bool:
        if not self.config_path:
            return False
        balance_cfg = self.config.get("balance", {})
        provider_cfg = balance_cfg.get(provider)
        if not isinstance(provider_cfg, dict):
            return False

        provider_cfg["verified_usage_cost"] = round(corrected_cost, 6)
        provider_cfg["verified_usage_note"] = (
            f"Auto-calibrated from polled balance at {time.strftime('%Y-%m-%d %H:%M:%S')} UTC."
        )
        try:
            with open(self.config_path, "w", encoding="utf-8") as f:
                yaml.dump(self.config, f, default_flow_style=False, sort_keys=False)
            return True
        except Exception as exc:
            logger.error("Failed to persist auto-calibration: %s", exc)
            return False

    def _provider_deposits(self, provider: str) -> Optional[float]:
        balance_cfg = self.config.get("balance", {})
        provider_cfg = balance_cfg.get(provider)
        if not isinstance(provider_cfg, dict):
            return None

        if provider_cfg.get("projects"):
            total = 0.0
            for proj in provider_cfg["projects"].values():
                if not isinstance(proj, dict):
                    continue
                for entry in proj.get("ledger", []) or []:
                    total += float(entry.get("amount", 0.0) or 0.0)
            return total

        if "ledger" not in provider_cfg:
            return None
        return sum(float(entry.get("amount", 0.0) or 0.0) for entry in provider_cfg.get("ledger", []) or [])

    def _computed_provider_cost(self, provider: str) -> float:
        aliases = _PROVIDER_ALIASES.get(provider, [provider])
        placeholders = ",".join("?" * len(aliases))
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(
                f"SELECT COALESCE(SUM(cost_total), 0) FROM records WHERE provider IN ({placeholders})",
                aliases,
            )
            value = cursor.fetchone()[0] or 0.0
        return float(value)

    def _insert_snapshot(self, snapshot: Dict[str, Any]):
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO resource_snapshots (
                    provider, snapshot_type, timestamp, balance_amount, balance_currency,
                    balance_source, tier, total_credits, rpm_limit, rpm_used, rpm_remaining,
                    computed_cost, drift_amount, drift_percentage, raw_response
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    snapshot.get("provider"),
                    snapshot.get("snapshot_type"),
                    snapshot.get("timestamp"),
                    snapshot.get("balance_amount"),
                    snapshot.get("balance_currency"),
                    snapshot.get("balance_source"),
                    snapshot.get("tier"),
                    snapshot.get("total_credits"),
                    snapshot.get("rpm_limit"),
                    snapshot.get("rpm_used"),
                    snapshot.get("rpm_remaining"),
                    snapshot.get("computed_cost"),
                    snapshot.get("drift_amount"),
                    snapshot.get("drift_percentage"),
                    json.dumps(
                        {
                            "error": snapshot.get("error"),
                            "calibration_status": snapshot.get("calibration_status"),
                            "calibration_action": snapshot.get("calibration_action"),
                            "payload": snapshot.get("raw_response") or {},
                        }
                    ),
                ),
            )
            conn.commit()

    def get_latest_snapshots(self, providers: Optional[List[str]] = None) -> Dict[str, Dict[str, Any]]:
        where = ""
        params: List[Any] = []
        if providers:
            placeholders = ",".join("?" * len(providers))
            where = f"WHERE provider IN ({placeholders})"
            params.extend(providers)

        query = f"""
            SELECT rs.*
            FROM resource_snapshots rs
            INNER JOIN (
                SELECT provider, MAX(timestamp) AS max_ts
                FROM resource_snapshots
                {where}
                GROUP BY provider
            ) latest
            ON rs.provider = latest.provider AND rs.timestamp = latest.max_ts
        """
        latest: Dict[str, Dict[str, Any]] = {}
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute(query, params)
            for row in cursor.fetchall():
                raw_response = row["raw_response"]
                parsed_raw = {}
                if raw_response:
                    try:
                        parsed_raw = json.loads(raw_response)
                    except Exception:
                        parsed_raw = {"raw": raw_response}

                drift_pct = row["drift_percentage"]
                status = "ok"
                if drift_pct is not None:
                    if abs(drift_pct) >= self.autocorrect_threshold_pct:
                        status = "critical"
                    elif abs(drift_pct) >= self.alert_threshold_pct:
                        status = "warn"

                latest[row["provider"]] = {
                    "provider": row["provider"],
                    "snapshot_type": row["snapshot_type"],
                    "timestamp": row["timestamp"],
                    "balance_amount": row["balance_amount"],
                    "balance_currency": row["balance_currency"],
                    "balance_source": row["balance_source"],
                    "tier": row["tier"],
                    "total_credits": row["total_credits"],
                    "rpm_limit": row["rpm_limit"],
                    "rpm_used": row["rpm_used"],
                    "rpm_remaining": row["rpm_remaining"],
                    "computed_cost": row["computed_cost"],
                    "drift_amount": row["drift_amount"],
                    "drift_percentage": row["drift_percentage"],
                    "status": status,
                    "error": parsed_raw.get("error"),
                    "raw_payload": parsed_raw.get("payload") if isinstance(parsed_raw, dict) else {},
                }
        return latest

    @staticmethod
    def _parse_balance_text(text: str) -> Dict[str, Optional[float]]:
        if not text:
            return {"amount": None, "currency": None}
        normalized = text.replace("\xa0", " ")
        match = re.search(r"([￥¥$€£])\s*([0-9][0-9,]*(?:\.[0-9]+)?)", normalized)
        if match:
            symbol = match.group(1)
            amount = float(match.group(2).replace(",", ""))
            currency = {"$": "USD", "¥": "CNY", "￥": "CNY", "€": "EUR", "£": "GBP"}.get(symbol)
            return {"amount": amount, "currency": currency}

        return {"amount": None, "currency": None}

    @staticmethod
    def _parse_elevenlabs_subscription_text(text: str) -> Dict[str, Optional[float]]:
        if not text:
            return {"plan_name": None, "remaining_credits": None, "total_credits": None}

        normalized = re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()
        plan_name: Optional[str] = None
        remaining_credits: Optional[float] = None
        total_credits: Optional[float] = None
        used_credits: Optional[float] = None

        def _to_num(value: str) -> float:
            return float(value.replace(",", ""))

        # e.g. "98,432 of 100,000 credits"
        of_match = re.search(
            r"([0-9][0-9,]*)\s+of\s+([0-9][0-9,]*)\s*credits?",
            normalized,
            flags=re.IGNORECASE,
        )
        if of_match:
            remaining_credits = _to_num(of_match.group(1))
            total_credits = _to_num(of_match.group(2))

        # e.g. "used 1,568 of 100,000"
        if total_credits is None or remaining_credits is None:
            used_of_match = re.search(
                r"used[^0-9]{0,10}([0-9][0-9,]*)\s+of\s+([0-9][0-9,]*)",
                normalized,
                flags=re.IGNORECASE,
            )
            if used_of_match:
                used_credits = _to_num(used_of_match.group(1))
                total_credits = _to_num(used_of_match.group(2))
                remaining_credits = max(total_credits - used_credits, 0.0)

        slash_match = re.search(r"([0-9][0-9,]*)\s*/\s*([0-9][0-9,]*)\s*credits?", normalized, flags=re.IGNORECASE)
        if slash_match:
            remaining_credits = _to_num(slash_match.group(1))
            total_credits = _to_num(slash_match.group(2))

        if remaining_credits is None:
            remaining_match = re.search(r"(?:remaining|left)[^0-9]{0,20}([0-9][0-9,]*)", normalized, flags=re.IGNORECASE)
            if not remaining_match:
                remaining_match = re.search(r"([0-9][0-9,]*)\s*credits?\s*(?:remaining|left)", normalized, flags=re.IGNORECASE)
            if remaining_match:
                remaining_credits = _to_num(remaining_match.group(1))

        if total_credits is None:
            total_match = re.search(
                r"(?:monthly|per month|total|included|allowance|quota)[^0-9]{0,30}([0-9][0-9,]*)\s*credits?",
                normalized,
                flags=re.IGNORECASE,
            )
            if not total_match:
                total_match = re.search(r"([0-9][0-9,]*)\s*credits?\s*(?:monthly|included|total)", normalized, flags=re.IGNORECASE)
            if total_match:
                total_credits = _to_num(total_match.group(1))

        # Standalone large numbers near "credits" if explicit labels are missing.
        if remaining_credits is None or total_credits is None:
            credit_numbers: List[float] = []
            for match in re.finditer(r"([0-9][0-9,]{2,})\s*credits?", normalized, flags=re.IGNORECASE):
                credit_numbers.append(_to_num(match.group(1)))
            for match in re.finditer(r"credits?[^0-9]{0,20}([0-9][0-9,]{2,})", normalized, flags=re.IGNORECASE):
                credit_numbers.append(_to_num(match.group(1)))
            if credit_numbers:
                if remaining_credits is None:
                    remaining_credits = credit_numbers[0]
                if total_credits is None and len(credit_numbers) > 1:
                    total_credits = max(credit_numbers)
                if total_credits is None and used_credits is not None:
                    total_credits = credit_numbers[-1]
                if total_credits is not None and used_credits is not None and remaining_credits is None:
                    remaining_credits = max(total_credits - used_credits, 0.0)

        for known_plan in ["Creator", "Starter", "Pro", "Scale", "Enterprise"]:
            if re.search(rf"\b{known_plan}\b", normalized, flags=re.IGNORECASE):
                plan_name = known_plan
                break

        if not plan_name:
            plan_match = re.search(r"\b([A-Za-z][A-Za-z0-9 +_-]{1,30})\s+plan\b", normalized, flags=re.IGNORECASE)
            if plan_match:
                candidate = plan_match.group(1).strip()
                if candidate.lower() not in {"current", "subscription", "your", "the"}:
                    plan_name = candidate.title()
        if not plan_name:
            plan_match = re.search(
                r"\b(?:current|subscription)?\s*plan\s*[:\-]?\s*([A-Za-z][A-Za-z0-9_-]{1,30})\b",
                normalized,
                flags=re.IGNORECASE,
            )
            if plan_match:
                plan_name = plan_match.group(1).strip().title()

        return {
            "plan_name": plan_name,
            "remaining_credits": remaining_credits,
            "total_credits": total_credits,
        }

    @staticmethod
    def _parse_first_int(text: str, pattern: str) -> Optional[int]:
        if not text:
            return None
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if not match:
            return None
        try:
            return int(match.group(1).replace(",", ""))
        except Exception:
            return None

    @staticmethod
    def _parse_first_match(text: str, pattern: str) -> Optional[str]:
        if not text:
            return None
        match = re.search(pattern, text, flags=re.IGNORECASE)
        return match.group(1) if match else None
