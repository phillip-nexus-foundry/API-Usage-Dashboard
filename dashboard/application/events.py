"""
Lightweight in-process event bus for decoupling components.
No external dependencies (no Redis, no message queue).
"""
import asyncio
import logging
from collections import defaultdict
from typing import Callable, Any

logger = logging.getLogger(__name__)


class EventBus:
    """Simple async pub/sub within the FastAPI process."""

    # Event types
    RECORDS_INGESTED = "records_ingested"
    BALANCE_CHECKED = "balance_checked"
    PRICING_CHANGED = "pricing_changed"
    DRIFT_DETECTED = "drift_detected"
    RECONCILIATION_COMPLETE = "reconciliation_complete"

    def __init__(self):
        self._handlers: dict[str, list[Callable]] = defaultdict(list)

    def subscribe(self, event_type: str, handler: Callable):
        """Register a handler for an event type."""
        self._handlers[event_type].append(handler)
        logger.debug(f"Subscribed {handler.__name__} to {event_type}")

    def unsubscribe(self, event_type: str, handler: Callable):
        """Remove a handler."""
        if handler in self._handlers[event_type]:
            self._handlers[event_type].remove(handler)

    async def publish(self, event_type: str, data: dict = None):
        """Publish an event to all subscribers. Non-blocking for async handlers."""
        data = data or {}
        handlers = self._handlers.get(event_type, [])
        if not handlers:
            return

        logger.debug(f"Publishing {event_type} to {len(handlers)} handlers")
        for handler in handlers:
            try:
                if asyncio.iscoroutinefunction(handler):
                    await handler(data)
                else:
                    handler(data)
            except Exception as e:
                logger.error(f"Event handler {handler.__name__} failed on {event_type}: {e}")
