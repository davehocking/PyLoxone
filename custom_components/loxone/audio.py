"""Entry-owned optional Audioserver metadata, shared by the entry's players."""

from __future__ import annotations

import asyncio
from contextlib import suppress
import logging
from typing import TYPE_CHECKING
from urllib.parse import urljoin, urlsplit

from .pyloxone_api.audioserver import AudioServerListener, AudioZoneMetadata, audioserver_uri

if TYPE_CHECKING:
    from collections.abc import Callable

    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)


class AudioMetadataCoordinator:
    """Keep the event connection independent of ordinary Miniserver controls."""

    def __init__(self, host: str) -> None:
        """Prepare a receive-only listener without opening a connection."""
        self._listeners: dict[int, set[Callable[[], None]]] = {}
        self._client = AudioServerListener(host, self._on_update)
        self._http_origin = audioserver_uri(host).replace("ws://", "http://", 1)
        self._task: asyncio.Task[None] | None = None

    def async_start(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Let HA cancel and await the connection on entry unload or shutdown."""
        if self._task is not None and not self._task.done():
            return
        self._task = entry.async_create_background_task(
            hass, self._client.async_run(), name=f"loxone audio metadata {entry.entry_id}"
        )

    async def async_stop(self) -> None:
        """Cancel and await the connection before dropping entity subscriptions."""
        if self._task is not None:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        self._listeners.clear()

    def get(self, player_id: int) -> AudioZoneMetadata | None:
        """Return an exact player's current metadata, never a name-based guess."""
        return self._client.get(player_id)

    def artwork_url(self, cover_url: str | None) -> str | None:
        """Resolve optional artwork against this entry's configured Audioserver."""
        if not cover_url:
            return None
        try:
            resolved = urljoin(self._http_origin, cover_url)
            parsed = urlsplit(resolved)
            if (
                parsed.scheme not in ("http", "https")
                or not parsed.hostname
                or parsed.username is not None
                or parsed.password is not None
                or parsed.port == 0
            ):
                return None
        except ValueError:
            return None
        return resolved

    def async_add_listener(self, player_id: int, listener: Callable[[], None]) -> Callable[[], None]:
        """Subscribe one player; the entity owns its unsubscribe callback."""
        self._listeners.setdefault(player_id, set()).add(listener)

        def unsubscribe() -> None:
            if listeners := self._listeners.get(player_id):
                listeners.discard(listener)
            if player_id in self._listeners and not self._listeners[player_id]:
                del self._listeners[player_id]

        return unsubscribe

    def _on_update(self, updates: tuple[AudioZoneMetadata, ...]) -> None:
        """Notify affected players; an empty update invalidates every snapshot."""
        player_ids = {row.player_id for row in updates} if updates else set(self._listeners)
        for player_id in player_ids:
            for listener in tuple(self._listeners.get(player_id, ())):
                try:
                    listener()
                except Exception as err:  # noqa: BLE001
                    _LOGGER.warning("Audioserver metadata subscriber failed (%s)", type(err).__name__)
