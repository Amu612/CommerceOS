"""Settings entrypoint. Import `settings` from here everywhere."""

from app.core.settings.base import (
    BaseAppSettings,
    DevAppSettings,
    ProdAppSettings,
    TestAppSettings,
    get_settings,
)

settings: BaseAppSettings = get_settings()

__all__ = [
    "BaseAppSettings",
    "DevAppSettings",
    "ProdAppSettings",
    "TestAppSettings",
    "get_settings",
    "settings",
]
