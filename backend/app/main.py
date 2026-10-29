"""Production ASGI entry point: authenticated MySQL BFF + independent DeerFlow worker.

The old demo routes and question decision graph are no longer mounted.
"""
from app.employee.web import app, create_app

__all__ = ["app", "create_app"]
