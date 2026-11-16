"""Dependencies shared by the API routers."""

from __future__ import annotations

from dataclasses import dataclass

from app.earnings import EarningsMonitor
from app.online import OnlineStatusMonitor
from app.settings import Settings
from app.store import StreamerStore
from app.supervisor import Supervisor


@dataclass(frozen=True)
class AppContext:
    """Long-lived application objects handed to every router."""

    settings: Settings
    store: StreamerStore
    supervisor: Supervisor
    online_monitor: OnlineStatusMonitor
    earnings_monitor: EarningsMonitor
