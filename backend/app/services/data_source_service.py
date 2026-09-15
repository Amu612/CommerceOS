"""
Active data-source flag: which order/customer/product data every agent reads.

The platform has a single source of truth — the seeded Olist + DataCo replay
dataset ("historic") — surfaced via `simulated_clock()` and every agent's
`data_layer.py`/tools. This module is kept as a stable, always-available
import (several call sites — e.g. `app.agents.inventory.tools` — import it
unconditionally) so removing a *future* live source stays a one-file change
instead of touching every agent again; today `is_live()` always reports
False and `available_sources()` only ever offers "historic".
"""

from __future__ import annotations

import logging
from typing import Literal

DataSource = Literal["historic"]

logger = logging.getLogger(__name__)


class DataSourceService:
    def get_active_source(self) -> DataSource:
        return "historic"

    def set_active_source(self, source: str) -> DataSource:
        if source != "historic":
            raise ValueError(f"Unknown data source '{source}'. Only 'historic' is available.")
        return "historic"

    def available_sources(self) -> list[DataSource]:
        return ["historic"]

    def is_historic(self) -> bool:
        return True

    def is_live(self) -> bool:
        return False


data_source_service = DataSourceService()
