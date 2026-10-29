"""Entrypoint: run alembic upgrade head, then start uvicorn."""
from __future__ import annotations

import logging
import sys
import time

from alembic import command
from alembic.config import Config as AlembicConfig

from . import config

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("sync.run")


def migrate(retries: int = 30, delay: float = 2.0) -> None:
    """Apply migrations; retry until the DB is reachable (compose startup)."""
    cfg = AlembicConfig("alembic.ini")
    cfg.set_main_option("script_location", "alembic")
    last: Exception | None = None
    for _ in range(retries):
        try:
            command.upgrade(cfg, "head")
            return
        except Exception as e:  # noqa: BLE001 - DB may not be up yet
            last = e
            log.warning("migration not ready yet: %s", e)
            time.sleep(delay)
    # Fail-soft for the app overall: migrations are required to serve, but a
    # crash here only affects this service; report and exit non-zero.
    log.error("giving up on migrations: %s", last)
    sys.exit(1)


if __name__ == "__main__":
    migrate()
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=config.BIND_HOST,
        port=config.BIND_PORT,
        log_level="info",
    )
