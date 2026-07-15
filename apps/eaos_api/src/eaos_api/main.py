"""EAOS API server entrypoint.

Usage::

    uvicorn eaos_api.main:app --host 0.0.0.0 --port 8000

The module-level ``app`` is constructed at import time so uvicorn can pick it
up by attribute name. Configuration is loaded from environment variables
(via ``AppConfig.load_config``); the lifespan wires all real dependencies
onto ``app.state`` so the FastAPI routes in ``eaos.gateway.api.routes`` can
resolve them through the injectors in ``eaos.gateway.api.deps``.
"""

from __future__ import annotations

from eaos.core.config import AppConfig
from eaos.gateway.api.app import create_app

from eaos_api.lifespan import lifespan

config: AppConfig = AppConfig.load_config()
app = create_app(config)
# Override the lifespan created inside create_app with the production wiring.
app.router.lifespan_context = lifespan
