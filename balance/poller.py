"""
Background balance polling and drift calibration.
"""
import json
import logging
import re
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

logger = logging.getLogger(__name__)

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
            url="https://platform.moonshot.cn/console/wallet",
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
        return await self._poll_provider_page(
            provider="elevenlabs",
            url="https://elevenlabs.io/subscription",
            selectors=[
                "text=/\\$\\s*[0-9,.]+/",
                "[class*='balance']",
                "[class*='credit']",
            ],
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
                profile_dir = str(self.profiles_dir / provider)
                browser = await p.chromium.launch_persistent_context(
                    user_data_dir=profile_dir,
                    headless=True,
                    viewport={"width": 1400, "height": 900},
                )
                page = await browser.new_page()
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
                await browser.close()
        except Exception as exc:
            return self._error_snapshot(provider, str(exc), raw_response=raw_response)

        candidate_text = raw_response.get("selected_text") or page_text
        parsed = self._parse_balance_text(candidate_text or "")
        if parsed["amount"] is None:
            return self._error_snapshot(provider, "balance not found in page", raw_response=raw_response)

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
                    balance_source, tier, rpm_limit, rpm_used, rpm_remaining,
                    computed_cost, drift_amount, drift_percentage, raw_response
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    snapshot.get("provider"),
                    snapshot.get("snapshot_type"),
                    snapshot.get("timestamp"),
                    snapshot.get("balance_amount"),
                    snapshot.get("balance_currency"),
                    snapshot.get("balance_source"),
                    snapshot.get("tier"),
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
