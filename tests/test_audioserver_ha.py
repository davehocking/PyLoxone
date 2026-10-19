"""
Real HA lifecycle with fake transports and explicitly synthetic structure/events.

No household configuration, credentials, or real device captures are used.
"""

from __future__ import annotations

# Assert/magic fixture IDs, private lifecycle checks and stub exceptions are intentional.
# ruff: noqa: S101, INP001, PLR2004, SLF001, ARG001, TRY003, EM101

import asyncio
import copy
import json
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, Self
from unittest.mock import AsyncMock

import pytest
from homeassistant.components.media_player import MediaPlayerEntityFeature
from homeassistant.const import Platform
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.translation import async_get_translations
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components import loxone
from custom_components.loxone.const import DOMAIN, EVENT
from custom_components.loxone.coordinator import LoxoneCoordinator
from custom_components.loxone.pyloxone_api import audioserver

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from homeassistant.core import HomeAssistant

STRUCTURE = {
    "controls": {
        f"synthetic-zone-{player}": {
            "uuidAction": f"synthetic-zone-{player}",
            "name": "Duplicate zone name",
            "type": "AudioZoneV2",
            "details": {"playerid": player},
            "states": {"volume": f"volume-{player}", "playState": f"play-{player}"},
        }
        for player in (7, 8)
    },
    "rooms": {},
    "cats": {},
}


@pytest.fixture
async def audio_setup(
    hass: HomeAssistant, enable_custom_integrations: None, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[SimpleNamespace]:
    """Run the integration's actual setup and unload, replacing only transports."""
    opened = []
    closed = []
    queues = []
    apis = []

    class Socket:
        def __init__(self, uri: str) -> None:
            self.uri = uri
            self.queue = asyncio.Queue()
            queues.append(self.queue)

        async def __aenter__(self) -> Self:
            opened.append(self.uri)
            return self

        async def __aexit__(self, *_: object) -> None:
            closed.append(self.uri)

        async def __aiter__(self) -> AsyncIterator[str]:
            while True:
                message = await self.queue.get()
                if message is None:
                    return
                yield message

    async def miniserver_run(**_: Any) -> None:
        await asyncio.Future()

    async def first_refresh(coordinator: LoxoneCoordinator) -> None:
        coordinator.api = SimpleNamespace(
            run=miniserver_run,
            close=AsyncMock(),
            send_websocket_command=AsyncMock(),
            get_token_dict=lambda: {"token": "", "hash_alg": "", "valid_until": ""},
        )
        apis.append(coordinator.api)
        coordinator.miniserver = SimpleNamespace(
            lox_config=SimpleNamespace(json=copy.deepcopy(STRUCTURE)),
            miniserver_type=2,
        )
        coordinator._control_uuids = frozenset(STRUCTURE["controls"])

    monkeypatch.setattr(loxone, "LOXONE_PLATFORMS", [Platform.MEDIA_PLAYER])
    monkeypatch.setattr(LoxoneCoordinator, "async_config_entry_first_refresh", first_refresh)
    monkeypatch.setattr(audioserver, "connect", lambda uri, **_: Socket(uri))

    async def setup(
        host: str = "audio.example.invalid", *, title: str = "Synthetic Miniserver", playback: bool = False
    ) -> MockConfigEntry:
        entry = MockConfigEntry(
            domain=DOMAIN,
            title=title,
            version=4,
            options={
                "host": "miniserver.example.invalid",
                "port": 80,
                "username": "synthetic-user",
                "password": "synthetic-password",
                "audioserver_host": host,
                **({"audioserver_playback": True} if playback else {}),
            },
        )
        entry.add_to_hass(hass)
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        return entry

    entries = []

    async def tracked_setup(*args: Any, **kwargs: Any) -> MockConfigEntry:
        entry = await setup(*args, **kwargs)
        entries.append(entry)
        return entry

    yield SimpleNamespace(setup=tracked_setup, opened=opened, closed=closed, queues=queues, apis=apis)
    for entry in entries:
        await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


def zone_entity_id(hass: HomeAssistant, player: int | str) -> str | None:
    """Resolve the actual registry identity, never an entity-name assumption."""
    return er.async_get(hass).async_get_entity_id("media_player", DOMAIN, f"synthetic-zone-{player}")


@pytest.mark.parametrize(("service", "action"), [("media_play", "play"), ("media_pause", "pause")])
@pytest.mark.asyncio
async def test_optin_play_pause_uses_exact_audio_player_without_native_start(
    hass: HomeAssistant, audio_setup: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, service: str, action: str
) -> None:
    """Optin play pause uses exact audio player without native start."""
    command = AsyncMock()
    monkeypatch.setattr(audioserver.AudioServerCommands, "async_execute", command)
    await audio_setup.setup(playback=True)
    first = zone_entity_id(hass, 7)
    before = hass.states.get(first).state
    await hass.services.async_call("media_player", service, {"entity_id": first}, blocking=True)
    command.assert_awaited_once_with(7, action)
    audio_setup.apis[0].send_websocket_command.assert_not_awaited()
    assert hass.states.get(first).state == before


@pytest.mark.asyncio
async def test_playback_option_uses_current_token_and_is_closed_on_unload(
    hass: HomeAssistant, audio_setup: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Playback option uses current token and is closed on unload."""
    entry = await audio_setup.setup()
    form = await hass.config_entries.options.async_init(entry.entry_id)
    assert "audioserver_playback" in {str(key) for key in form["data_schema"].schema}
    result = await hass.config_entries.options.async_configure(
        form["flow_id"], {**entry.options, "audioserver_playback": True}
    )
    assert result["type"] == "create_entry"
    await hass.async_block_till_done()
    coordinator = hass.data[DOMAIN][entry.entry_id]
    client = coordinator.audio_commands
    audio_setup.apis[-1].get_token_dict = lambda: {"token": "synthetic-current-token"}
    assert client._credentials() == ("synthetic-user", "synthetic-current-token")
    audio_setup.apis[-1].get_token_dict = lambda: {"token": "synthetic-refreshed-token"}
    assert client._credentials() == ("synthetic-user", "synthetic-refreshed-token")
    closed = AsyncMock()
    monkeypatch.setattr(client, "async_close", closed)
    assert await hass.config_entries.async_unload(entry.entry_id)
    closed.assert_awaited_once()
    assert coordinator.audio_commands is None


@pytest.mark.asyncio
async def test_playback_option_requires_host(hass: HomeAssistant, audio_setup: SimpleNamespace) -> None:
    """Playback option requires host."""
    entry = await audio_setup.setup()
    form = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        form["flow_id"], {**entry.options, "audioserver_host": "", "audioserver_playback": True}
    )
    assert result["type"] == "form"
    assert result["errors"]["base"] == "audioserver_playback_requires_host"
    assert not entry.options.get("audioserver_playback")


@pytest.mark.parametrize("failure", ["missing_token", "negative_ack", "invalid_player", "missing_host"])
@pytest.mark.asyncio
async def test_enabled_playback_failure_never_falls_back_to_room_start(
    hass: HomeAssistant, audio_setup: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    """Enabled playback failure never falls back to room start."""
    if failure == "invalid_player":
        monkeypatch.setitem(STRUCTURE["controls"]["synthetic-zone-7"]["details"], "playerid", None)
    if failure == "negative_ack":
        monkeypatch.setattr(
            audioserver.AudioServerCommands,
            "async_execute",
            AsyncMock(side_effect=audioserver.AudioCommandError("Synthetic negative acknowledgement")),
        )
    await audio_setup.setup(host="" if failure == "missing_host" else "audio.example.invalid", playback=True)
    first = zone_entity_id(hass, 7)
    with pytest.raises(HomeAssistantError) as failure_info:
        await hass.services.async_call("media_player", "media_play", {"entity_id": first}, blocking=True)
    assert failure_info.value.translation_domain == DOMAIN
    assert failure_info.value.translation_key == (
        "audioserver_playback_unavailable" if failure == "missing_host" else "audioserver_playback_failed"
    )
    audio_setup.apis[0].send_websocket_command.assert_not_awaited()


@pytest.mark.asyncio
async def test_enabled_playback_keeps_volume_native(
    hass: HomeAssistant, audio_setup: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Enabled playback keeps volume native."""
    command = AsyncMock()
    monkeypatch.setattr(audioserver.AudioServerCommands, "async_execute", command)
    await audio_setup.setup(playback=True)
    first = zone_entity_id(hass, 7)
    hass.bus.async_fire(EVENT, {"play-7": 1, "volume-7": 36})
    await hass.async_block_till_done()
    await hass.services.async_call("media_player", "media_play", {"entity_id": first}, blocking=True)
    assert hass.states.get(first).attributes["volume_level"] == 0.36
    await hass.services.async_call(
        "media_player", "volume_set", {"entity_id": first, "volume_level": 0.41}, blocking=True
    )
    await hass.async_block_till_done()
    audio_setup.apis[0].send_websocket_command.assert_awaited_once_with("synthetic-zone-7", "volume/41")
    command.assert_awaited_once_with(7, "play")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "following_path",
    ["spotify:track:synthetic", "library:local:track:synthetic", "airplay", "tunein:synthetic", ""],
)
async def test_radio_skip_features_follow_exact_zone_source(
    hass: HomeAssistant, audio_setup: SimpleNamespace, following_path: str
) -> None:
    """Only evidenced Loxone Radio removes skip; source changes restore the baseline."""
    await audio_setup.setup()
    first = zone_entity_id(hass, 7)
    second = zone_entity_id(hass, 8)
    baseline = hass.states.get(first).attributes["supported_features"]
    skip = MediaPlayerEntityFeature.NEXT_TRACK | MediaPlayerEntityFeature.PREVIOUS_TRACK
    assert baseline & skip == skip
    hass.bus.async_fire(EVENT, {"play-7": 2, "play-8": 2, "volume-7": 36})
    await audio_setup.queues[0].put(
        '{"audio_event":[{"playerid":7,"mode":"play","audiopath":"loxoneradio:stream:synthetic"}]}'
    )
    await hass.async_block_till_done()
    radio = hass.states.get(first)
    assert radio.attributes["source"] == "Loxone Radio"
    assert radio.attributes["supported_features"] == baseline & ~skip
    assert radio.state == "playing"
    assert radio.attributes["volume_level"] == 0.36
    assert hass.states.get(second).attributes["supported_features"] == baseline
    await audio_setup.queues[0].put(
        json.dumps({"audio_event": [{"playerid": 7, "mode": "play", "audiopath": following_path}]})
    )
    await hass.async_block_till_done()
    assert hass.states.get(first).attributes["supported_features"] == baseline
    audio_setup.apis[0].send_websocket_command.assert_not_awaited()


@pytest.mark.asyncio
async def test_actual_ha_entities_receive_exact_player_metadata(
    hass: HomeAssistant, audio_setup: SimpleNamespace
) -> None:
    """Same display names must not route metadata to the wrong AudioZoneV2."""
    await audio_setup.setup()
    # This assertion fails before the optional connector is wired into setup.
    assert audio_setup.opened == ["ws://audio.example.invalid:7091/"]
    first = zone_entity_id(hass, 7)
    second = zone_entity_id(hass, 8)
    assert first != second
    # HA intentionally hides track attributes for OFF players. Playback state
    # remains authoritative on the independent Miniserver event path.
    hass.bus.async_fire(EVENT, {"play-7": 2, "play-8": 2})
    await hass.async_block_till_done()
    assert hass.states.get(first).state == "playing"
    await audio_setup.queues[0].put(
        json.dumps(
            {
                "audio_event": [
                    {
                        "playerid": 7,
                        "title": "Synthetic track",
                        "artist": "Synthetic artist",
                        "album": "Synthetic album",
                        "audiopath": "spotify:track:synthetic",
                    }
                ]
            }
        )
    )
    await hass.async_block_till_done()
    attributes = hass.states.get(first).attributes
    assert attributes["media_title"] == "Synthetic track"
    assert attributes["media_artist"] == "Synthetic artist"
    assert attributes["media_album_name"] == "Synthetic album"
    assert attributes["source"] == "Spotify"
    assert "media_title" not in hass.states.get(second).attributes
    assert len(audio_setup.opened) == 1


@pytest.mark.asyncio
async def test_artwork_is_resolved_on_the_configured_audio_origin(
    hass: HomeAssistant, audio_setup: SimpleNamespace
) -> None:
    """Relative covers use the Audioserver, never the Miniserver's address."""
    await audio_setup.setup()
    first = zone_entity_id(hass, 7)
    hass.bus.async_fire(EVENT, {"play-7": 2})
    await audio_setup.queues[0].put(
        '{"audio_event":[{"playerid":7,"title":"Artwork test","coverurl":"/covers/synthetic.jpg"}]}'
    )
    await hass.async_block_till_done()
    entity = hass.data["media_player"].get_entity(first)
    assert entity.media_image_url == "http://audio.example.invalid:7091/covers/synthetic.jpg"
    assert hass.states.get(first).attributes["entity_picture"].startswith("/api/media_player_proxy/")


@pytest.mark.asyncio
async def test_new_track_invalidates_artwork_cache_even_with_the_same_url(
    hass: HomeAssistant, audio_setup: SimpleNamespace
) -> None:
    """Some servers reuse a player cover URL across tracks."""
    await audio_setup.setup()
    first = zone_entity_id(hass, 7)
    hass.bus.async_fire(EVENT, {"play-7": 2})
    hashes = []
    for title in ("First synthetic track", "Second synthetic track"):
        await audio_setup.queues[0].put(
            json.dumps({"audio_event": [{"playerid": 7, "title": title, "coverurl": "/covers/player-7.jpg"}]})
        )
        await hass.async_block_till_done()
        hashes.append(hass.data["media_player"].get_entity(first).media_image_hash)
    assert hashes[0] != hashes[1]


@pytest.mark.asyncio
async def test_options_flow_can_enable_then_disable_metadata(hass: HomeAssistant, audio_setup: SimpleNamespace) -> None:
    """The real options flow reloads the entry; blank host restores opt-out."""
    entry = await audio_setup.setup(host="")
    assert audio_setup.opened == []
    form = await hass.config_entries.options.async_init(entry.entry_id)
    assert "audioserver_host" in {str(key) for key in form["data_schema"].schema}
    result = await hass.config_entries.options.async_configure(
        form["flow_id"],
        user_input={**entry.options, "audioserver_host": "audio.example.invalid"},
    )
    assert result["type"] == "create_entry"
    await hass.async_block_till_done()
    assert entry.options["audioserver_host"] == "audio.example.invalid"
    assert audio_setup.opened == ["ws://audio.example.invalid:7091/"]
    form = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        form["flow_id"], user_input={**entry.options, "audioserver_host": ""}
    )
    assert result["type"] == "create_entry"
    await hass.async_block_till_done()
    assert not entry.options.get("audioserver_host")
    assert audio_setup.closed == audio_setup.opened
    assert len(audio_setup.opened) == 1


@pytest.mark.asyncio
async def test_options_reject_invalid_audio_host_without_reloading(
    hass: HomeAssistant, audio_setup: SimpleNamespace
) -> None:
    """Optional metadata must not accept URLs, credentials or custom paths."""
    entry = await audio_setup.setup(host="")
    form = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        form["flow_id"], user_input={**entry.options, "audioserver_host": "ws://user:password@audio/bad"}
    )
    assert result["type"] == "form"
    assert result["errors"] == {"base": "invalid_audioserver_host"}
    await hass.async_block_till_done()
    assert not entry.options.get("audioserver_host")
    assert audio_setup.opened == []


@pytest.mark.asyncio
async def test_invalid_saved_audio_option_keeps_existing_players(
    hass: HomeAssistant, audio_setup: SimpleNamespace
) -> None:
    """Even bypassed/old invalid options cannot take down normal media controls."""
    await audio_setup.setup(host="ws://not-a-bare-host")
    first = zone_entity_id(hass, 7)
    assert first is not None
    assert hass.states.get(first) is not None
    assert audio_setup.opened == []
    await hass.services.async_call("media_player", "media_play", {"entity_id": first}, blocking=True)
    await hass.async_block_till_done()
    audio_setup.apis[0].send_websocket_command.assert_awaited_once_with("synthetic-zone-7", "play")


@pytest.mark.asyncio
async def test_coordinator_cleanup_explicitly_closes_optional_audio(
    hass: HomeAssistant, audio_setup: SimpleNamespace
) -> None:
    """Connection teardown does not rely solely on HA's eventual task sweep."""
    entry = await audio_setup.setup()
    coordinator = hass.data[DOMAIN][entry.entry_id]
    await coordinator.async_cleanup()
    assert audio_setup.closed == audio_setup.opened
    assert coordinator.audio_metadata is None
    audio_setup.apis[0].close.assert_awaited_once()


@pytest.mark.asyncio
async def test_starting_shared_listener_twice_does_not_duplicate_connections(
    hass: HomeAssistant, audio_setup: SimpleNamespace
) -> None:
    """There is one task for all zones of an entry, even if start is repeated."""
    entry = await audio_setup.setup()
    audio = hass.data[DOMAIN][entry.entry_id].audio_metadata
    audio.async_start(hass, entry)
    audio.async_start(hass, entry)
    await hass.async_block_till_done()
    assert audio_setup.opened == ["ws://audio.example.invalid:7091/"]


@pytest.mark.asyncio
async def test_music_hint_is_removed_on_event_unknown_and_disconnect(
    hass: HomeAssistant, audio_setup: SimpleNamespace
) -> None:
    """Exercise the real HA state path, not an attribute-only surrogate."""
    await audio_setup.setup()
    first = zone_entity_id(hass, 7)
    hass.bus.async_fire(EVENT, {"play-7": 2, "volume-7": 36})
    for row, expected in [
        ({"artist": "Synthetic artist", "album": "Synthetic album"}, "music"),
        ({"eventype": 3, "mode": "play", "title": "Synthetic TTS"}, None),
        ({"audiopath": "spotify:track:synthetic"}, "music"),
        ({"title": "Unknown title only"}, None),
        ({"audiopath": "airplay", "type": 7, "mode": "play"}, None),
    ]:
        await audio_setup.queues[0].put(json.dumps({"audio_event": [{"playerid": 7, **row}]}))
        await hass.async_block_till_done()
        assert hass.states.get(first).attributes.get("media_content_type") == expected
        assert hass.states.get(first).attributes["volume_level"] == 0.36
    await audio_setup.queues[0].put(None)
    await hass.async_block_till_done()
    assert "media_content_type" not in hass.states.get(first).attributes
    assert hass.states.get(first).state == "playing"


@pytest.mark.asyncio
@pytest.mark.parametrize("language", ["en", "de", "cs"])
async def test_audio_translation_keys_exist_and_load_in_real_ha(
    hass: HomeAssistant, enable_custom_integrations: None, language: str
) -> None:
    """Each shipped language supplies all added audio labels/errors, not fallback English."""
    raw = json.loads((Path(loxone.__file__).parent / "translations" / f"{language}.json").read_text())
    for category, step in (("config", "user"), ("options", "init")):
        labels = await async_get_translations(hass, language, category, {DOMAIN})
        for key in ("audioserver_host", "audioserver_playback"):
            assert raw[category]["step"][step]["data"][key]
            label = f"component.loxone.{category}.step.{step}.data.{key}"
            assert labels[label] == raw[category]["step"][step]["data"][key]
        for key in ("invalid_audioserver_host", "audioserver_playback_requires_host"):
            assert raw[category]["error"][key]
            assert labels[f"component.loxone.{category}.error.{key}"] == raw[category]["error"][key]
    errors = await async_get_translations(hass, language, "exceptions", {DOMAIN})
    for key in ("audioserver_playback_failed", "audioserver_playback_unavailable"):
        assert errors[f"component.loxone.exceptions.{key}.message"] == raw["exceptions"][key]["message"]


@pytest.mark.asyncio
async def test_optional_audio_fields_have_real_ha_ui_translations(
    hass: HomeAssistant, enable_custom_integrations: None
) -> None:
    """Both setup and options forms need a label and a useful validation error."""
    for category, step in (("config", "user"), ("options", "init")):
        labels = await async_get_translations(hass, "en", category, {DOMAIN})
        label = f"component.loxone.{category}.step.{step}.data.audioserver_host"
        error = f"component.loxone.{category}.error.invalid_audioserver_host"
        assert label in labels
        assert "optional" in labels[label].lower()
        assert error in labels


@pytest.mark.asyncio
async def test_disconnect_clears_metadata_without_affecting_controls(
    hass: HomeAssistant, audio_setup: SimpleNamespace
) -> None:
    """An optional socket loss must not mark the native player unavailable."""
    await audio_setup.setup()
    first = zone_entity_id(hass, 7)
    hass.bus.async_fire(EVENT, {"play-7": 2, "volume-7": 36})
    await audio_setup.queues[0].put(
        '{"audio_event":[{"playerid":7,"title":"Before disconnect","audiopath":"spotify:synthetic"}]}'
    )
    await hass.async_block_till_done()
    assert hass.states.get(first).attributes["media_title"] == "Before disconnect"
    await audio_setup.queues[0].put(None)
    await hass.async_block_till_done()
    state = hass.states.get(first)
    assert "media_title" not in state.attributes
    assert "source" not in state.attributes
    assert state.state == "playing"
    assert state.attributes["volume_level"] == 0.36
    await hass.services.async_call("media_player", "media_pause", {"entity_id": first}, blocking=True)
    await hass.async_block_till_done()
    audio_setup.apis[0].send_websocket_command.assert_awaited_once_with("synthetic-zone-7", "pause")


@pytest.mark.asyncio
async def test_reload_keeps_entity_ids_and_replaces_all_subscriptions(
    hass: HomeAssistant, audio_setup: SimpleNamespace
) -> None:
    """Reload closes the old socket and does not retain old entities/listeners."""
    entry = await audio_setup.setup()
    first = zone_entity_id(hass, 7)
    old_audio = hass.data[DOMAIN][entry.entry_id].audio_metadata
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    new_audio = hass.data[DOMAIN][entry.entry_id].audio_metadata
    assert new_audio is not old_audio
    assert old_audio._listeners == {}
    assert len(new_audio._listeners) == 2
    assert len(audio_setup.opened) == 2
    assert len(audio_setup.closed) == 1
    assert zone_entity_id(hass, 7) == first
    hass.bus.async_fire(EVENT, {"play-7": 2})
    await audio_setup.queues[1].put('{"audio_event":[{"playerid":7,"title":"After reload"}]}')
    await hass.async_block_till_done()
    assert hass.states.get(first).attributes["media_title"] == "After reload"
    assert old_audio.get(7) is None


@pytest.mark.asyncio
async def test_unavailable_audio_does_not_block_setup_or_native_commands(
    hass: HomeAssistant, audio_setup: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Connection refusal is background metadata failure, not core unavailability."""

    def refuse(*_: Any, **__: Any) -> None:
        raise OSError("synthetic connection refused")

    monkeypatch.setattr(audioserver, "connect", refuse)
    entry = await audio_setup.setup()
    first = zone_entity_id(hass, 7)
    assert hass.data[DOMAIN][entry.entry_id].connection_available
    assert hass.states.get(first).state != "unavailable"
    await hass.services.async_call("media_player", "media_play", {"entity_id": first}, blocking=True)
    await hass.async_block_till_done()
    audio_setup.apis[0].send_websocket_command.assert_awaited_once_with("synthetic-zone-7", "play")


@pytest.mark.asyncio
async def test_multiple_entries_keep_player_ids_and_connections_isolated(
    hass: HomeAssistant, audio_setup: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The same numeric player ID on another server is not the same player."""
    first_entry = await audio_setup.setup()
    second_structure = copy.deepcopy(STRUCTURE)
    second_controls = {}
    for key, control in second_structure["controls"].items():
        control["uuidAction"] += "-b"
        control["states"] = {name: value + "-b" for name, value in control["states"].items()}
        second_controls[key + "-b"] = control
    second_structure["controls"] = second_controls
    monkeypatch.setitem(globals(), "STRUCTURE", second_structure)
    second_entry = await audio_setup.setup(host="second-audio.example.invalid", title="Synthetic Miniserver B")
    first = zone_entity_id(hass, 7)
    second = zone_entity_id(hass, "7-b")
    hass.bus.async_fire(EVENT, {"play-7": 2, "play-7-b": 2})
    await audio_setup.queues[0].put(
        '{"audio_event":[{"playerid":7,"title":"First server track","eventype":100,"mode":"play"}]}'
    )
    await hass.async_block_till_done()
    assert hass.states.get(first).attributes["media_title"] == "First server track"
    assert "media_title" not in hass.states.get(second).attributes
    assert hass.states.get(first).attributes["audio_event"] == "alarm"
    assert hass.states.get(second).attributes["audio_event_active"] is None
    await audio_setup.queues[0].put('{"audio_event":[{"playerid":7}]}')
    await audio_setup.queues[1].put(
        '{"audio_event":[{"playerid":7,"title":"Second server track","eventype":3,"mode":"play"}]}'
    )
    await hass.async_block_till_done()
    old_audio = hass.data[DOMAIN][first_entry.entry_id].audio_metadata
    timers = tuple(old_audio._client._receiver._end_timers.values())
    assert len(timers) == 1
    assert await hass.config_entries.async_unload(first_entry.entry_id)
    await hass.async_block_till_done()
    assert audio_setup.closed == ["ws://audio.example.invalid:7091/"]
    assert all(timer.cancelled() for timer in timers)
    assert hass.states.get(second).attributes["media_title"] == "Second server track"
    assert hass.states.get(second).attributes["audio_event"] == "tts"
    assert hass.states.get(second).attributes["audio_event_active"] is True
    assert hass.data[DOMAIN][second_entry.entry_id].audio_metadata.get(7).title == "Second server track"
    await hass.services.async_call("media_player", "media_pause", {"entity_id": second}, blocking=True)
    await hass.async_block_till_done()
    audio_setup.apis[1].send_websocket_command.assert_awaited_once_with("synthetic-zone-7-b", "pause")
    audio_setup.apis[0].send_websocket_command.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("details", [None, {}, {"playerid": True}, {"playerid": 7.5}, {"playerid": "wrong"}])
async def test_missing_or_invalid_player_mapping_preserves_native_zone(
    hass: HomeAssistant, audio_setup: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, details: object
) -> None:
    """Never infer a player ID from a name, order, or malformed structure."""
    monkeypatch.setitem(STRUCTURE["controls"]["synthetic-zone-7"], "details", details)
    await audio_setup.setup()
    first = zone_entity_id(hass, 7)
    hass.bus.async_fire(EVENT, {"play-7": 2})
    await audio_setup.queues[0].put('{"audio_event":[{"playerid":7,"title":"Must not attach"}]}')
    await hass.async_block_till_done()
    assert hass.states.get(first).state == "playing"
    assert "media_title" not in hass.states.get(first).attributes
    assert "audio_event" not in hass.states.get(first).attributes
    assert "audio_event_active" not in hass.states.get(first).attributes


@pytest.mark.asyncio
async def test_audio_event_attributes_are_per_zone_and_preserve_controls(
    hass: HomeAssistant, audio_setup: SimpleNamespace
) -> None:
    """Target activity is visible even with duplicate names and independent native state."""
    await audio_setup.setup()
    first = zone_entity_id(hass, 7)
    second = zone_entity_id(hass, 8)
    assert hass.states.get(first).attributes["audio_event_active"] is None
    hass.bus.async_fire(EVENT, {"play-7": 2, "play-8": 2, "volume-7": 36})
    await audio_setup.queues[0].put(
        json.dumps(
            {
                "audio_event": [
                    {"playerid": 7, "eventype": 3, "mode": "play", "title": "Synthetic TTS"},
                    {"playerid": 8, "eventype": 3, "mode": "stop", "title": "Synthetic TTS"},
                ]
            }
        )
    )
    await hass.async_block_till_done()
    state = hass.states.get(first)
    assert state.attributes["audio_event"] == "tts"
    assert state.attributes["audio_event_active"] is True
    assert state.attributes["uuid"] == "synthetic-zone-7"
    assert state.attributes["platform"] == "loxone"
    assert state.attributes["volume_level"] == 0.36
    assert state.state == "playing"
    assert hass.states.get(second).attributes["audio_event_active"] is False
    assert hass.states.get(second).attributes["audio_event"] is None
    assert "media_title" not in hass.states.get(second).attributes
    await hass.services.async_call("media_player", "media_pause", {"entity_id": first}, blocking=True)
    await hass.async_block_till_done()
    audio_setup.apis[0].send_websocket_command.assert_awaited_once_with("synthetic-zone-7", "pause")
    await audio_setup.queues[0].put('{"audio_event":[{"playerid":7,"mode":"stop"}]}')
    await hass.async_block_till_done()
    assert hass.states.get(first).attributes["audio_event_active"] is False
    assert hass.states.get(first).attributes["audio_event"] is None


@pytest.mark.asyncio
async def test_alarm_end_timer_updates_real_ha_without_another_frame(
    hass: HomeAssistant, audio_setup: SimpleNamespace
) -> None:
    """The actual loop callback, not just parsing, must publish the stable end."""
    await audio_setup.setup()
    first = zone_entity_id(hass, 7)
    await audio_setup.queues[0].put('{"audio_event":[{"playerid":7,"eventype":100,"mode":"play"}]}')
    await hass.async_block_till_done()
    assert hass.states.get(first).attributes["audio_event"] == "alarm"
    await audio_setup.queues[0].put(
        '{"audio_event":[{"playerid":7,"mode":"stop","title":"Stable track","coverurl":"/covers/stable.jpg"}]}'
    )
    await hass.async_block_till_done()
    assert hass.states.get(first).attributes["audio_event_active"] is True
    entity = hass.data["media_player"].get_entity(first)
    before_hash = entity.media_image_hash
    assert before_hash is not None
    await asyncio.sleep(1.55)
    await hass.async_block_till_done()
    assert hass.states.get(first).attributes["audio_event_active"] is False
    assert hass.states.get(first).attributes["audio_event"] is None
    assert entity.media_image_hash == before_hash


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["disconnect", "reload", "unload"])
async def test_pending_alarm_timer_is_cancelled_by_ha_lifecycle(
    hass: HomeAssistant, audio_setup: SimpleNamespace, action: str
) -> None:
    """Exercise real teardown with an outstanding timer, not only idle sockets."""
    entry = await audio_setup.setup()
    first = zone_entity_id(hass, 7)
    old_audio = hass.data[DOMAIN][entry.entry_id].audio_metadata
    await audio_setup.queues[0].put('{"audio_event":[{"playerid":7,"eventype":100,"mode":"play"}]}')
    await hass.async_block_till_done()
    await audio_setup.queues[0].put('{"audio_event":[{"playerid":7,"mode":"stop"}]}')
    await hass.async_block_till_done()
    receiver = old_audio._client._receiver
    timers = tuple(receiver._end_timers.values())
    assert len(timers) == 1
    assert hass.states.get(first).attributes["audio_event_active"] is True
    if action == "disconnect":
        await audio_setup.queues[0].put(None)
    elif action == "reload":
        assert await hass.config_entries.async_reload(entry.entry_id)
    else:
        assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert receiver._end_timers == {}
    assert all(timer.cancelled() for timer in timers)
    assert old_audio.get(7) is None
    if action != "unload":
        assert hass.states.get(first).attributes["audio_event_active"] is None
        assert hass.states.get(first).attributes["audio_event"] is None
        assert hass.states.get(first).state != "unavailable"
    if action != "disconnect":
        assert old_audio._listeners == {}


@pytest.mark.asyncio
async def test_event_attributes_absent_when_listener_is_disabled(
    hass: HomeAssistant, audio_setup: SimpleNamespace
) -> None:
    """Opt-out remains native-only, not a permanently unknown optional feature."""
    await audio_setup.setup(host="")
    first = zone_entity_id(hass, 7)
    assert "audio_event" not in hass.states.get(first).attributes
    assert "audio_event_active" not in hass.states.get(first).attributes


@pytest.mark.parametrize("language", ["en", "de", "cs"])
async def test_audio_error_catalogues_resolve_through_ha(
    hass: HomeAssistant, audio_setup: SimpleNamespace, language: str
) -> None:
    """Exercise HA's real loader, not merely translation-key presence on an error."""
    await audio_setup.setup()
    resources = await async_get_translations(hass, language, "exceptions", {DOMAIN})
    for key in ("audioserver_playback_failed", "audioserver_playback_unavailable"):
        message = resources[f"component.{DOMAIN}.exceptions.{key}.message"]
        assert isinstance(message, str)
        assert len(message) > 40
        assert "Audio Server" in message
    for category in ("config", "options"):
        resources = await async_get_translations(hass, language, category, {DOMAIN})
        for key in ("invalid_audioserver_host", "audioserver_playback_requires_host"):
            assert resources[f"component.{DOMAIN}.{category}.error.{key}"]
