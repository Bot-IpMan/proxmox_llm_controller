"""Convenience exports for the FastAPI controller package."""

# Re-export the main FastAPI application module so consumers can simply do
# ``from controller import app`` without modifying ``sys.path`` logic in tests
# or integration scripts.  This mirrors the behaviour of a traditional Python
# package with an ``app`` submodule while keeping the project layout flat.
from . import app as app

__all__ = ["app"]
