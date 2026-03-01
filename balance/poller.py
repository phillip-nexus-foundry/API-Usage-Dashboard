"""
Background balance polling and drift calibration.
"""
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
        return await self._poll_provider_page(
            provider="moonshot",
            url="https://platform.moonshot.ai/console/account",
            selectors=[
                "text=/[\\u00a5$]\\s*[0-9,.]+/",
                "text=/balance|credit|wallet/i",
                ".balance",
                "[class*='balance']",
                "[class*='wallet']",
            ],
        )

    async def poll_anthropic(self) -> Dict[str, Any]:
        return await self._poll_provider_page(
            provider="anthropic",
            url="https://console.anthropic.com/settings/billing",
            selectors=[
                "[data-testid='balance-display']",
                "[data-testid='credits-balance']",
                "text=/\\$\\s*[0-9,.]+/",
            ],
        )

    async def poll_minimax(self) -> Dict[str, Any]:
        return await self._poll_provider_page(
            provider="minimax",
            url="https://www.minimaxi.com/platform",
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
                browser = await p.chromium.launch_persistent_context(
                    user_data_dir=profile_dir,
                    headless=True,
                    viewport={"width": 1400, "height": 900},
                )
                page = await browser.new_page()
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
                page_text = await page.inner_text("body")
                raw_response["body_length"] = len(page_text or "")
                raw_response["body_preview"] = (page_text or "")[:500]
                await browser.close()
        except Exception as exc:
            return self._error_snapshot(
                "elevenlabs",
                f"Browser scrape failed: {exc}. Set XI_API_KEY env var for reliable polling.",
                raw_response=raw_response,
            )

        # Detect login redirect (only if page is short / clearly a login form)
        if len(page_text or "") < 2000:
            lower_start = (page_text or "").lower()
            if "sign in" in lower_start or "log in" in lower_start or "create account" in lower_start:
                return self._error_snapshot(
                    "elevenlabs",
                    "Not logged in. Re-login to browser profile or set XI_API_KEY env var.",
                    raw_response=raw_response,
                )

        parsed = self._parse_elevenlabs_subscription_text(page_text or "")
        if parsed["remaining_credits"] is None:
            return self._error_snapshot(
                "elevenlabs",
                "Credits not found on page. Set XI_API_KEY env var for reliable polling.",
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
        Placeholder poller until Codex CLI exposes a direct balance endpoint.
        """
        return self._build_snapshot(
            provider="codex_cli",
            snapshot_type="usage_status",
            balance_amount=1000.0,
            balance_currency="credits",
            balance_source="simulated",
            raw_response={"note": "Placeholder Codex CLI credits snapshot"},
        )

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
        try:
            async with async_playwright() as p:
                # Connect to running Brave instance via CDP (shares login sessions)
                try:
                    browser = await p.chromium.connect_over_cdp(
                        f"http://127.0.0.1:{_BRAVE_CDP_PORT}",
                        timeout=10000,
                    )
                    context = browser.contexts[0]  # Use Brave's default context (has cookies)
                except Exception as cdp_exc:
                    # Fallback: launch isolated Chromium if Brave CDP not available
                    logger.warning(
                        "CDP connection to Brave failed (%s), falling back to isolated browser",
                        cdp_exc,
                    )
                    context = await p.chromium.launch_persistent_context(
                        user_data_dir=str(self.profiles_dir / provider),
                        headless=True,
                        viewport={"width": 1400, "height": 900},
                    )
                    browser = None  # context IS the browser in persistent mode

                page = await context.new_page()
                await page.goto(url, wait_until="domcontentloaded", timeout=30000)
                await page.wait_for_timeout(1200)

                selected_text = None
                for selector in selectors:
                    try:
                        await page.wait_for_selector(selector, timeout=6000)
                        selected_text = await page.locator(selector).first.inner_text()
                        if selected_text:
                            break
                    except Exception:
                        continue

                page_text = await page.inner_text("body")
                raw_response["selected_text"] = selected_text
                await page.close()
                if browser:
                    await browser.close()  # Disconnect CDP (does NOT close Brave)
                else:
                    await context.close()  # Close isolated browser
        except Exception as exc:
            return self._error_snapshot(provider, str(exc), raw_response=raw_response)

        candidate_text = raw_response.get("selected_text") or page_text
        parsed = self._parse_balance_text(candidate_text or "")
        if parsed["amount"] is None:
            # Check if we landed on a login page (short body with auth keywords)
            lower_page = (page_text or "").lower()
            is_login_page = (
                len(page_text or "") < 2000
                and any(phrase in lower_page for phrase in ["sign in", "log in", "create account"])
            )
            if is_login_page:
                return self._error_snapshot(
                    provider,
                    f"Not logged in to {provider}. Open browser profile and log in.",
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

        fallback = re.search(r"\b([0-9][0-9,]*(?:\.[0-9]+)?)\b", normalized)
        if fallback:
            return {"amount": float(fallback.group(1).replace(",", "")), "currency": None}
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
