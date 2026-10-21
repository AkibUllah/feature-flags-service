"""Minimal server-side Python client: polls the bulk payload with ETag, evaluates locally."""

from flagclient.client import FlagClient, FlagClientError

__all__ = ["FlagClient", "FlagClientError"]
__version__ = "0.1.0"
