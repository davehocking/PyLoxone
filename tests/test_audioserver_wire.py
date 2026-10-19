"""Real local WebSocket framing with synthetic data, not Audioserver hardware."""
# Assertions and protocol fixture IDs are intentional; tests are a namespace.
# ruff: noqa: S101, INP001

from __future__ import annotations

import asyncio
import base64
import json
from urllib.parse import unquote

from Crypto.Cipher import AES, PKCS1_v1_5
from Crypto.PublicKey import RSA
from Crypto.Util.Padding import unpad

import os
from pathlib import Path
import tempfile
from typing import TYPE_CHECKING, Any

import pytest
import pytest_socket
from websockets.asyncio.client import unix_connect
from websockets.asyncio.server import unix_serve
from websockets.typing import Subprotocol

from custom_components.loxone.pyloxone_api import audioserver

if TYPE_CHECKING:
    from websockets.asyncio.server import ServerConnection

    from custom_components.loxone.pyloxone_api.audioserver import AudioZoneMetadata


@pytest.mark.asyncio
async def test_listener_uses_real_websocket_handshake_and_frames(monkeypatch: pytest.MonkeyPatch) -> None:
    """Exercise the real library over AF_UNIX; no DNS, TCP or household access."""
    # phcc's autouse fixture resets connect guards without forwarding the CLI
    # Unix flag. Permit only AF_UNIX here; creation of TCP sockets stays blocked.
    pytest_socket.socket_allow_hosts([], allow_unix_socket=True)
    received = asyncio.Event()
    closed = asyncio.Event()
    commands = []
    titles = []

    async def handler(socket: ServerConnection) -> None:
        assert socket.subprotocol == "remotecontrol"
        await socket.send('{"audio_event":[{"playerid":7,"title":"Real framing, invented track"}]}')
        commands.extend([message async for message in socket])
        closed.set()

    def on_update(updates: tuple[AudioZoneMetadata, ...]) -> None:
        if updates:
            snapshot = listener.get(7)
            assert snapshot is not None
            titles.append(snapshot.title)
            received.set()

    # Explicit TMPDIR keeps this probe in the caller's managed scratch area.
    with tempfile.TemporaryDirectory(prefix="audio-ws-", dir=os.environ.get("TMPDIR")) as directory:
        path = str(Path(directory) / "ws.sock")
        async with await unix_serve(handler, path, subprotocols=[Subprotocol("remotecontrol")], ping_interval=None):

            def connect(uri: str, **kwargs: Any) -> Any:
                return unix_connect(path=path, uri=uri, **kwargs)

            monkeypatch.setattr(audioserver, "connect", connect)
            listener = audioserver.AudioServerListener("audio.example.invalid", on_update)
            task = asyncio.create_task(listener.async_run())
            try:
                await asyncio.wait_for(received.wait(), timeout=2)
                assert titles == ["Real framing, invented track"]
            finally:
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            await asyncio.wait_for(closed.wait(), timeout=2)
    assert commands == []
    assert listener.get(7) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["play", "pause"])
@pytest.mark.parametrize("ack", [True, False])
async def test_player_commands_use_real_authenticated_websocket_frames(
    monkeypatch: pytest.MonkeyPatch, action: str, *, ack: bool
) -> None:
    """Decrypt a synthetic session over AF_UNIX; never open household TCP."""
    pytest_socket.socket_allow_hosts([], allow_unix_socket=True)
    key = RSA.generate(2048)
    commands = []
    authenticated = []
    closed = asyncio.Event()

    async def handler(socket: ServerConnection) -> None:
        try:
            assert socket.subprotocol == "remotecontrol"
            await socket.send("Session-Token: synthetic-wire-session")
            commands.append(await socket.recv())
            assert commands[-1] == "audio/cfg/getkey/full"
            await socket.send(json.dumps({"getkey_result": [{"pubkey": key.publickey().export_key().decode()}]}))
            auth = await socket.recv()
            _, _, username, envelope, ciphertext = auth.split("/")
            assert unquote(username) == "synthetic-user"
            decoded = PKCS1_v1_5.new(key).decrypt(base64.b64decode(unquote(envelope)), b"invalid")
            aes_key, iv, session = decoded.decode().split(":")
            assert session == "synthetic-wire-session"
            cipher = AES.new(bytes.fromhex(aes_key), AES.MODE_CBC, bytes.fromhex(iv))
            assert (
                unpad(cipher.decrypt(base64.b64decode(unquote(ciphertext))), AES.block_size) == b"synthetic-wire-token"
            )
            authenticated.append(True)
            await socket.send('{"authenticate_result":"authentication successful"}')
            commands.append(await socket.recv())
            assert commands[-1] == f"audio/13/{action}"
            await socket.send('{"audio_event":[{"playerid":999,"mode":"play"}]}')
            await socket.send(json.dumps({f"{action}_result": ack}))
            await socket.wait_closed()
        finally:
            closed.set()

    with tempfile.TemporaryDirectory(prefix="audio-command-ws-", dir=os.environ.get("TMPDIR")) as directory:
        path = str(Path(directory) / "ws.sock")
        async with await unix_serve(handler, path, subprotocols=[Subprotocol("remotecontrol")], ping_interval=None):

            def connect(uri: str, **kwargs: Any) -> Any:
                return unix_connect(path=path, uri=uri, **kwargs)

            monkeypatch.setattr(audioserver, "connect", connect)
            client = audioserver.AudioServerCommands(
                "audio.example.invalid", lambda: ("synthetic-user", "synthetic-wire-token")
            )
            if ack:
                await client.async_execute(13, action)
            else:
                with pytest.raises(audioserver.AudioCommandError):
                    await client.async_execute(13, action)
            await asyncio.wait_for(closed.wait(), timeout=2)
            await client.async_close()
    assert authenticated == [True]
    assert commands == ["audio/cfg/getkey/full", f"audio/13/{action}"]
