"""EAOS evolution worker entrypoint.

Usage::

    python -m eaos_worker

The worker polls ``harness.evolution_strategies`` for pending strategies and
advances them through the six-step governance pipeline. Configuration is
loaded from environment variables via ``AppConfig.load_config``.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import signal

from eaos.core.config import AppConfig

from eaos_worker.runner import main_async

logger = logging.getLogger(__name__)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    config = AppConfig.load_config()

    stop_event = asyncio.Event()
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    # Unix supports loop.add_signal_handler for graceful shutdown; Windows
    # does not and falls back to KeyboardInterrupt.
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError, RuntimeError):
            loop.add_signal_handler(sig, stop_event.set)

    try:
        loop.run_until_complete(
            main_async(config, stop_event=stop_event)
        )
    except KeyboardInterrupt:
        logger.info("Worker interrupted")
    finally:
        loop.close()


if __name__ == "__main__":
    main()
