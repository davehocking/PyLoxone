"""
Audioserver metadata/events and explicitly opt-in exact-player playback.

Event shapes follow the author-reported contract in PyLoxone issue #523.
The parser/receiver sends nothing. The optional connection owner performs only
the WebSocket handshake and receives events; it never authenticates or controls.
AudioServerCommands is a separate lazy connection used only for explicit actions.
"""

from __future__ import annotations

import asyncio
import base64
from dataclasses import dataclass, replace
from inspect import signature
from ipaddress import IPv6Address, ip_address
import json
import logging
import re
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

from Crypto.Cipher import AES, PKCS1_v1_5
from Crypto.PublicKey import RSA
from Crypto.Random import get_random_bytes
from Crypto.Util.Padding import pad
from websockets.asyncio.client import connect
from websockets.exceptions import WebSocketException
from websockets.typing import Subprotocol

if TYPE_CHECKING:
    from collections.abc import AsyncIterable, Callable

_LOGGER = logging.getLogger(__name__)
_MAX_HOSTNAME_LENGTH = 253
_AIRPLAY_ANNOUNCEMENT = 7
# Event names adapted from SebastianEitzert's Apache-2.0 POC in #523,
# comment 6251138699. See tests/fixtures/audioserver/README.md for provenance.
_EVENT_NAMES = {
    1: "doorbell",
    2: "alarm_clock",
    3: "tts",
    5: "custom_sound",
    7: "announcement",
    100: "alarm",
    101: "fire_alarm",
}
# Adapted from the contributor's measured flapping sequence, not a safety signal.
_DEBOUNCED_EVENTS = frozenset({"alarm", "fire_alarm", "alarm_clock"})
_EVENT_END_DELAY = 1.5
# websockets 15 enables environment proxies by default; 14 has no proxy option.
# Keep the declared >=14,<16 range while connecting directly to the selected host.
_DIRECT_CONNECT: dict[str, Any] = {"proxy": None} if "proxy" in signature(connect).parameters else {}


def audioserver_uri(host: str) -> str:
    """Build a fixed-port event origin from a bare hostname or IP address."""
    message = "Audioserver host must be a hostname or IP address without a port or path"
    if not isinstance(host, str) or not host or "%" in host:
        raise ValueError(message)
    bracketed = host.startswith("[") and host.endswith("]")
    address = host[1:-1] if bracketed else host
    try:
        parsed = ip_address(address)
    except ValueError:
        labels = host.removesuffix(".").split(".")
        if len(host) > _MAX_HOSTNAME_LENGTH or not all(
            re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", label) for label in labels
        ):
            raise ValueError(message) from None
        authority = host
    else:
        is_ipv6 = isinstance(parsed, IPv6Address)
        if bracketed and not is_ipv6:
            raise ValueError(message)
        authority = f"[{address}]" if is_ipv6 else address
    return f"ws://{authority}:7091/"


@dataclass(frozen=True, slots=True)
class AudioZoneMetadata:
    """Metadata for one exact Audioserver player, not a room display name."""

    player_id: int
    title: str | None = None
    artist: str | None = None
    album: str | None = None
    cover_url: str | None = None
    audio_path: str | None = None
    source: str | None = None
    event: str | None = None
    event_active: bool | None = None


def _no_event(code: object) -> bool:
    """Only absent/null or an integer zero explicitly denotes no event."""
    return code is None or (type(code) is int and code == 0)


def _event_code(row: dict[str, Any]) -> object:
    """Use native events first, with the narrowly reported AirPlay type7 variant."""
    code = row.get("eventype")
    # #523 comment 5984328342 reports AirPlay announcements with type, not
    # eventype. Other sources also use type, so it is NOT a generic event code.
    if (
        _no_event(code)
        and row.get("audiopath") == "airplay"
        and type(row.get("type")) is int
        and row["type"] == _AIRPLAY_ANNOUNCEMENT
    ):
        return _AIRPLAY_ANNOUNCEMENT
    return code


def _event(row: dict[str, Any]) -> tuple[str | None, bool | None]:
    """Qualify events by actual per-zone playback, not broadcast data alone."""
    code = _event_code(row)
    if _no_event(code):
        return None, False
    if type(code) is not int or code < 0:
        return None, None
    mode = row.get("mode")
    if mode == "play":
        return _EVENT_NAMES.get(code, f"event_{code}"), True
    return None, False if mode in ("pause", "stop") else None


def _source(row: dict[str, Any]) -> str | None:
    """Identify reported sources without treating audio type zero as Spotify."""
    path = _text(row.get("audiopath")) or ""
    if path == "airplay":
        return "AirPlay"
    if path.startswith("library:local:track:") and path.removeprefix("library:local:track:").strip():
        return "Local Library"
    for prefix, label in (
        ("spotify:", "Spotify"),
        ("tunein:", "TuneIn"),
        ("loxoneradio:", "Loxone Radio"),
        ("linein:", "Line-In"),
    ):
        if path.startswith(prefix):
            return label
    if not path and (_text(row.get("sourceName")) or "").casefold() == "spotify":
        return "Spotify"
    return None


def _text(value: object) -> str | None:
    """Retain original non-blank strings; reject malformed optional fields."""
    return value if isinstance(value, str) and value.strip() else None


def audio_player_id(value: object) -> int | None:
    """Accept positive numeric IDs without coercing bools or fractional values."""
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        return None
    if isinstance(value, str) and not (value.isascii() and value.isdecimal()):
        return None
    try:
        result = int(value)
    except ValueError:
        return None
    return result if result > 0 else None


def audio_control_command(player_id: object, action: str) -> tuple[str, str]:
    """Build only reviewed player-scoped commands; continuity needs a live test."""
    target = audio_player_id(player_id)
    if target is None or action not in ("play", "pause"):
        message = "Audio playback requires an exact player ID and play/pause action"
        raise ValueError(message)
    return f"audio/{target}/{action}", f"{action}_result"


def parse_audio_events(payload: object) -> tuple[AudioZoneMetadata, ...]:
    """Extract valid independent zone snapshots, ignoring non-event replies."""
    if not isinstance(payload, dict) or not isinstance(payload.get("audio_event"), list):
        return ()
    updates = []
    for row in payload["audio_event"]:
        if not isinstance(row, dict) or (player_id := audio_player_id(row.get("playerid"))) is None:
            continue
        event, event_active = _event(row)
        is_event = not _no_event(_event_code(row))
        # TTS is broadcast to stopped non-target zones too. Neither its title
        # nor its artwork is evidence of that zone playing the interruption.
        display = row if not is_event or event_active is True else {}
        updates.append(
            AudioZoneMetadata(
                player_id=player_id,
                title=_text(display.get("title")),
                artist=_text(display.get("artist")),
                album=_text(display.get("album")),
                cover_url=_text(display.get("coverurl")),
                audio_path=_text(display.get("audiopath")),
                source=None if is_event else _source(row),
                event=event,
                event_active=event_active,
            ),
        )
    return tuple(updates)


class AudioCommandError(Exception):
    """A player command was not positively acknowledged; never implies retry."""


class AudioServerCommands:
    """
    Explicit opt-in commands, separate from the receive-only listener.

    Reuse the integration's *current* Miniserver token, not its password or a
    separately stored credential. Each explicit action gets one short-lived,
    authenticated connection. Do not retry writes or fall back to room play:
    room play can replace the selected source with the default favourite.
    Wire contract follows the installed vendor client and the existing native
    SDK's RSA-PKCS1v15/AES-CBC authentication, using existing dependencies only.
    """

    def __init__(self, host: str, credentials: Callable[[], tuple[str, str]], *, timeout: float = 15.0) -> None:
        """Create a lazy, serial command client without connecting or sending."""
        self._uri = audioserver_uri(host)
        self._credentials = credentials
        self._timeout = timeout
        self._lock = asyncio.Lock()
        self._closed = False
        self._socket: Any = None

    async def async_close(self) -> None:
        """Reject queued/future requests and close an in-flight owned socket."""
        self._closed = True
        if self._socket is not None:
            await self._socket.close()

    async def async_execute(self, player_id: object, action: str) -> None:
        """Send one exact-player play/pause and require a literal true reply."""
        command, reply_key = audio_control_command(player_id, action)
        owned_socket = None
        try:
            async with asyncio.timeout(self._timeout), self._lock:
                if self._closed:
                    message = "Audio playback connection is closed"
                    raise AudioCommandError(message)
                username, token = self._credentials()
                if not isinstance(username, str) or not username.strip() or not isinstance(token, str) or not token:
                    message = "Audio playback credentials are unavailable"
                    raise AudioCommandError(message)
                async with connect(
                    self._uri,
                    subprotocols=[Subprotocol("remotecontrol")],
                    open_timeout=self._timeout,
                    close_timeout=2,
                    ping_interval=None,
                    max_size=1024 * 1024,
                    **_DIRECT_CONNECT,
                ) as socket:
                    owned_socket = self._socket = socket
                    if self._closed:
                        message = "Audio playback connection is closed"
                        raise AudioCommandError(message)
                    await self._authenticate(socket, username, token)
                    if self._closed:
                        message = "Audio playback connection is closed"
                        raise AudioCommandError(message)
                    await socket.send(command)
                    if await self._reply(socket, reply_key) is not True:
                        message = "Audio playback command was not acknowledged"
                        raise AudioCommandError(message)
        except (OSError, WebSocketException, TimeoutError, ValueError, TypeError, KeyError, IndexError):  # fmt: skip
            # Neither native errors nor authentication envelopes may reach logs.
            message = "Audio playback failed; no fallback or automatic retry was sent"
            raise AudioCommandError(message) from None
        finally:
            if self._socket is owned_socket:
                self._socket = None

    async def _authenticate(self, socket: Any, username: str, token: str) -> None:
        """Authenticate the owned socket without sending a player command."""
        greeting = await socket.recv()
        if not isinstance(greeting, str) or "Session-Token:" not in greeting:
            message = "Audio playback session is unavailable"
            raise AudioCommandError(message)
        session = greeting.split("Session-Token:", 1)[1].splitlines()[0].strip()
        if not session:
            message = "Audio playback session is unavailable"
            raise AudioCommandError(message)
        await socket.send("audio/cfg/getkey/full")
        keys = await self._reply(socket, "getkey_result")
        if not isinstance(keys, list) or not keys or not isinstance(keys[0], dict):
            message = "Audio playback key is unavailable"
            raise AudioCommandError(message)
        key_pem = keys[0].get("pubkey")
        if not isinstance(key_pem, str) or not key_pem:
            message = "Audio playback key is unavailable"
            raise AudioCommandError(message)
        # Native firmware can return a single-line PEM; PyCryptodome's parser
        # requires whitespace after the header and before the footer.
        key_pem = key_pem.replace("-----BEGIN PUBLIC KEY-----", "-----BEGIN PUBLIC KEY-----\n").replace(
            "-----END PUBLIC KEY-----", "\n-----END PUBLIC KEY-----"
        )
        public_key = RSA.import_key(key_pem)
        key, iv = get_random_bytes(32), get_random_bytes(16)
        envelope = f"{key.hex()}:{iv.hex()}:{session}".encode()
        rsa_part = PKCS1_v1_5.new(public_key).encrypt(envelope)
        encrypted = AES.new(key, AES.MODE_CBC, iv).encrypt(pad(token.encode(), AES.block_size))
        encoded = [
            quote(username, safe=""),
            *(quote(base64.b64encode(part).decode(), safe="") for part in (rsa_part, encrypted)),
        ]
        await socket.send("secure/authenticate/" + "/".join(encoded))
        if await self._reply(socket, "authenticate_result") != "authentication successful":
            message = "Audio playback authentication was rejected"
            raise AudioCommandError(message)

    @staticmethod
    async def _reply(socket: Any, key: str) -> Any:
        """Receive under the caller's deadline, tolerating interleaved event frames."""
        while True:
            # recv() can complete immediately from a busy socket's buffer. Yield
            # so cancellation and the command-wide timeout cannot be starved.
            await asyncio.sleep(0)
            message = await socket.recv()
            if not isinstance(message, str):
                continue
            try:
                reply = json.loads(message)
            except (ValueError, TypeError):  # fmt: skip
                continue
            if isinstance(reply, dict) and key in reply:
                return reply[key]


class AudioEventReceiver:
    """
    Consume one externally owned text stream without any send capability.

    Empty update tuples invalidate all metadata on disconnect/reconnect. The
    caller owns connection establishment, backoff and HA-tracked task lifetime.
    No initial metadata is invented before an event for that exact player.
    Only flapping event-end attributes are held; zone metadata remains current.
    All pending end timers are cancelled before invalidating a lost stream.
    """

    def __init__(self, on_update: Callable[[tuple[AudioZoneMetadata, ...]], None]) -> None:
        """Create a receiver whose notifications run on its owning event loop."""
        self._on_update = on_update
        self._metadata: dict[int, AudioZoneMetadata] = {}
        self._end_timers: dict[int, asyncio.TimerHandle] = {}
        self._listening = False

    def get(self, player_id: int) -> AudioZoneMetadata | None:
        """Return the latest known snapshot, or unknown after loss of the stream."""
        return self._metadata.get(player_id)

    def _reset(self) -> None:
        """Invalidate stale snapshots rather than presenting them as current."""
        for timer in self._end_timers.values():
            timer.cancel()
        self._end_timers.clear()
        self._metadata.clear()
        self._on_update(())

    def _apply(self, row: AudioZoneMetadata) -> AudioZoneMetadata:
        """Publish metadata promptly, stabilising only known flapping event ends."""
        player_id = row.player_id
        old = self._metadata.get(player_id)
        if row.event_active is False and old is not None and old.event in _DEBOUNCED_EVENTS:
            if player_id not in self._end_timers:
                self._end_timers[player_id] = asyncio.get_running_loop().call_later(
                    _EVENT_END_DELAY, self._finish_event, player_id
                )
            row = replace(row, event=old.event, event_active=True)
        elif timer := self._end_timers.pop(player_id, None):
            # A renewed/replaced event or unknown state invalidates a pending end.
            timer.cancel()
        self._metadata[player_id] = row
        return row

    def _finish_event(self, player_id: int) -> None:
        """End a stable interruption without restoring an older metadata snapshot."""
        self._end_timers.pop(player_id, None)
        row = replace(self._metadata[player_id], event=None, event_active=False)
        self._metadata[player_id] = row
        self._on_update((row,))

    async def async_listen(self, messages: AsyncIterable[str | bytes]) -> None:
        """Read passive events; cancellation propagates after stale-data cleanup."""
        if self._listening:
            message = "Audio event receiver is already listening"
            raise RuntimeError(message)
        self._listening = True
        try:
            self._reset()
            async for frame in messages:
                if not isinstance(frame, str):
                    continue
                try:
                    payload = json.loads(frame)
                except ValueError:
                    # Includes malformed JSON and Python's oversized-integer limit.
                    continue
                if updates := parse_audio_events(payload):
                    updates = tuple(self._apply(row) for row in updates)
                    self._on_update(updates)
        finally:
            self._listening = False
            self._reset()


class AudioServerListener:
    """Own an optional, unauthenticated, receive-only event connection."""

    def __init__(self, host: str, on_update: Callable[[tuple[AudioZoneMetadata, ...]], None]) -> None:
        """Create the connection owner without opening a socket."""
        self._uri = audioserver_uri(host)
        self._on_update = on_update
        self._retry_delay = 1
        self._receiver = AudioEventReceiver(self._receive_update)

    def _receive_update(self, updates: tuple[AudioZoneMetadata, ...]) -> None:
        """Reset backoff only after a valid event, not a flapping handshake."""
        if updates:
            self._retry_delay = 1
        self._on_update(updates)

    def get(self, player_id: int) -> AudioZoneMetadata | None:
        """Return metadata only while its event stream is current."""
        return self._receiver.get(player_id)

    async def async_run(self) -> None:
        """Receive events until the owning HA entry cancels this coroutine."""
        while True:
            try:
                async with connect(
                    self._uri,
                    subprotocols=[Subprotocol("remotecontrol")],
                    ping_interval=None,
                    open_timeout=10,
                    close_timeout=5,
                    max_size=1048576,
                    **_DIRECT_CONNECT,
                ) as socket:
                    await self._receiver.async_listen(socket)
            except (OSError, TimeoutError, WebSocketException) as err:
                # Never log raw frames, server replies or credential-bearing URLs.
                _LOGGER.debug("Audioserver event connection lost (%s)", type(err).__name__)
            await asyncio.sleep(self._retry_delay)
            self._retry_delay = min(self._retry_delay * 2, 30)
