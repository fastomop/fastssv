"""FastSSV HTTP API.

Provides a FastAPI service that wraps the static validator.
Run with: ``gunicorn -k uvicorn.workers.UvicornWorker fastssv.api.app:app``
"""

from fastssv.api.app import app, create_app

__all__ = ["app", "create_app"]
