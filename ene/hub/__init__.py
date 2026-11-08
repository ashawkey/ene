"""Web hub: serves the browser UI and attaches live sessions for it.

- :mod:`ene.hub.server` — FastAPI app, browser auth, session registry, relay.
- :mod:`ene.hub.link` — the hub's attachment to one live worker.
- :mod:`ene.hub.files` — read-only workspace browsing for the file panel.

The browser UI itself lives in ``ene/web`` (built into ``ene/web/dist``).
"""

from ene.hub.server import DEFAULT_HUB_PORT, Hub, RemoteSession, discover_hub, read_hub_info

__all__ = ["DEFAULT_HUB_PORT", "Hub", "RemoteSession", "discover_hub", "read_hub_info"]
