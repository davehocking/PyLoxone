"""Support for Loxone Audio zone media player."""

from __future__ import annotations

from dataclasses import replace
from hashlib import sha256
import logging
from typing import Any

from homeassistant.components.media_player import (
    MediaPlayerDeviceClass,
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
    MediaType,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType

from . import LoxoneEntity
from .audio import AudioMetadataCoordinator
from .const import (
    CONF_AUDIOSERVER_HOST,
    CONF_AUDIOSERVER_PLAYBACK,
    DEFAULT_AUDIO_ZONE_V2_PLAY_STATE,
    DOMAIN,
    SENDDOMAIN,
)
from .helpers import add_room_and_cat_to_value_values, get_all, get_or_create_device
from .miniserver import get_miniserver_from_hass
from .pyloxone_api.audioserver import AudioCommandError, AudioServerCommands, audio_player_id

_LOGGER = logging.getLogger(__name__)

PARALLEL_UPDATES = 0
DEFAULT_FORCE_UPDATE = False

# This is the "optimistic" view of supported features and will be returned until the
# actual set of supported feature have been determined (will always be all or a subset
# of these).
SUPPORT_LOXONE_AUDIO_ZONE = (
    MediaPlayerEntityFeature.PAUSE
    | MediaPlayerEntityFeature.PLAY
    | MediaPlayerEntityFeature.NEXT_TRACK
    | MediaPlayerEntityFeature.PREVIOUS_TRACK
    | MediaPlayerEntityFeature.VOLUME_SET
    | MediaPlayerEntityFeature.VOLUME_STEP
    | MediaPlayerEntityFeature.STOP
)


def _state_uuid(states: dict, name: str) -> str | None:
    """Un-guarded ``states[name]`` indexing crashes the media player platform when a
    structure file omits an attribute; return ``None`` instead."""
    return states.get(name)


def play_state_to_media_player_state(play_state: int) -> MediaPlayerState:
    """Map a Loxone AudioZoneV2 ``playState`` value to the matching HA state.

    Unknown values fall back to ``IDLE`` (a non-playing state): the entity
    must never be left without a state just because the server sent a new
    value we do not know about.
    """
    match play_state:
        case 0:
            return MediaPlayerState.IDLE
        case 1:
            return MediaPlayerState.PAUSED
        case 2:
            return MediaPlayerState.PLAYING
        case -1:
            return MediaPlayerState.OFF
        case _:
            _LOGGER.debug("Unknown playState %r; defaulting to %s", play_state, MediaPlayerState.IDLE)
            return MediaPlayerState.IDLE


def audio_zone_stop_value() -> str:
    """Command value behind the ``STOP`` feature.

    AudioZoneV2 has no ``stop`` sub-command; pausing makes the zone silent,
    which is the closest stop semantic the device offers.
    """
    return "pause"


async def async_setup_platform(
    hass: HomeAssistant,
    config: ConfigType,
    async_add_entities: AddEntitiesCallback,
    discovery_info: DiscoveryInfoType | None = None,
) -> None:
    """Set up Loxone Audio zones."""


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Load Loxone Audio zones based on a config entry."""
    miniserver = get_miniserver_from_hass(hass, config_entry)
    loxconfig = miniserver.lox_config.json
    entities = []

    audio_metadata = None
    if host := config_entry.options.get(CONF_AUDIOSERVER_HOST):
        try:
            audio_metadata = AudioMetadataCoordinator(host)
        except ValueError:
            _LOGGER.warning("Invalid optional Audioserver host; keeping Miniserver-only media controls")
        else:
            hass.data[DOMAIN][config_entry.entry_id].audio_metadata = audio_metadata

    coordinator = hass.data[DOMAIN][config_entry.entry_id]
    playback_enabled = config_entry.options.get(CONF_AUDIOSERVER_PLAYBACK, False) is True
    audio_commands = None
    if playback_enabled:
        try:
            audio_commands = AudioServerCommands(
                config_entry.options.get(CONF_AUDIOSERVER_HOST, ""),
                lambda: (config_entry.options.get("username", ""), coordinator.api.get_token_dict().get("token", "")),
            )
        except ValueError:
            _LOGGER.warning("Optional Audio Server playback unavailable; play/pause will not fall back to room start")
        else:
            coordinator.audio_commands = audio_commands

    for audioZone in get_all(loxconfig, "AudioZoneV2"):
        audioZone = add_room_and_cat_to_value_values(loxconfig, audioZone)
        audioZone.update(
            {
                "hass": hass,
            }
        )
        entities.append(
            LoxoneAudioZoneV2(
                audio_metadata=audio_metadata,
                audio_commands=audio_commands,
                audio_commands_enabled=playback_enabled,
                **audioZone,
            )
        )

    async_add_entities(entities)
    if audio_metadata is not None:
        audio_metadata.async_start(hass, config_entry)


class LoxoneAudioZoneV2(LoxoneEntity, MediaPlayerEntity):
    """Representation of a AudioZoneV2 Loxone device."""

    def __init__(
        self,
        audio_metadata: AudioMetadataCoordinator | None = None,
        *,
        audio_commands: AudioServerCommands | None = None,
        audio_commands_enabled: bool = False,
        **kwargs: Any,
    ) -> None:
        """Retain native controls and optionally bind an exact metadata player ID."""
        _LOGGER.debug("Input AudioZoneV2 uuidAction=%s", kwargs.get("uuidAction"))
        super().__init__(**kwargs)
        self.hass = kwargs["hass"]

        self._attr_device_class = MediaPlayerDeviceClass.SPEAKER
        self._state = play_state_to_media_player_state(DEFAULT_AUDIO_ZONE_V2_PLAY_STATE)
        self._volume = 0
        self._audio_metadata = audio_metadata
        self._audio_commands = audio_commands
        self._audio_commands_enabled = audio_commands_enabled
        details = kwargs.get("details")
        self._audio_player_id = audio_player_id(details.get("playerid")) if isinstance(details, dict) else None

        self.type = "AudioZoneV2"
        self._attr_device_info = get_or_create_device(self.unique_id, self.name, self.type, self.room)

    async def async_added_to_hass(self) -> None:
        """Keep the existing Miniserver listener and add exact-player metadata."""
        await super().async_added_to_hass()
        if self._audio_metadata is not None and self._audio_player_id is not None:
            self.async_on_remove(
                self._audio_metadata.async_add_listener(self._audio_player_id, self._update_audio_metadata)
            )
            self._update_audio_metadata()

    def _update_audio_metadata(self) -> None:
        """Clear attributes on stream loss without changing state or controls."""
        if self._audio_metadata is None or self._audio_player_id is None:
            return
        metadata = self._audio_metadata.get(self._audio_player_id)
        self._attr_media_title = metadata.title if metadata else None
        self._attr_media_artist = metadata.artist if metadata else None
        self._attr_media_album_name = metadata.album if metadata else None
        # Match the tested pilot: HA's native music subtitle needs this hint.
        # Clear it on events, unsupported/unknown metadata and stream loss.
        self._attr_media_content_type = (
            MediaType.MUSIC
            if metadata
            and metadata.event_active is False
            and (
                metadata.artist
                or metadata.album
                or metadata.source in {"Spotify", "AirPlay", "TuneIn", "Loxone Radio"}
            )
            else None
        )
        self._attr_media_image_url = self._audio_metadata.artwork_url(metadata.cover_url if metadata else None)
        # A player-specific image endpoint can keep its URL when the track changes.
        # Event-only timer transitions must not invalidate unchanged artwork.
        artwork_metadata = replace(metadata, event=None, event_active=None) if metadata else None
        self._attr_media_image_hash = (
            sha256(repr(artwork_metadata).encode()).hexdigest() if self._attr_media_image_url else None
        )
        self._attr_source = metadata.source if metadata else None
        # Preserve common Loxone attributes and native playback state. None is
        # unknown (including disconnect), not an observed event completion.
        self._attr_extra_state_attributes.update(
            audio_event=metadata.event if metadata else None,
            audio_event_active=metadata.event_active if metadata else None,
        )
        self.async_schedule_update_ha_state()

    async def event_handler(self, event):
        should_update = False

        if (u := _state_uuid(self.states, "volume")) and u in event.data:
            self._volume = float(event.data[u]) / 100
            should_update = True

        if (u := _state_uuid(self.states, "playState")) and u in event.data:
            self._state = play_state_to_media_player_state(event.data[u])
            should_update = True

        if should_update:
            self.async_schedule_update_ha_state()

    # properties
    @property
    def state(self) -> MediaPlayerState:
        """Return the playback state."""
        return self._state

    @property
    def volume_level(self) -> float | None:
        """Volume level of the media player (0..1)."""
        return self._volume

    @property
    def supported_features(self) -> MediaPlayerEntityFeature:
        """Report native controls, excluding skips for an evidenced radio stream."""
        if self.source == "Loxone Radio":
            return SUPPORT_LOXONE_AUDIO_ZONE & ~(
                MediaPlayerEntityFeature.NEXT_TRACK | MediaPlayerEntityFeature.PREVIOUS_TRACK
            )
        return SUPPORT_LOXONE_AUDIO_ZONE

    # commands
    async def _async_audio_command(self, action: str) -> bool:
        """Use explicitly enabled player commands without a source-changing fallback."""
        if not self._audio_commands_enabled:
            return False
        if self._audio_commands is None:
            raise HomeAssistantError(translation_domain=DOMAIN, translation_key="audioserver_playback_unavailable")
        try:
            await self._audio_commands.async_execute(self._audio_player_id, action)
        except (AudioCommandError, ValueError):
            raise HomeAssistantError(translation_domain=DOMAIN, translation_key="audioserver_playback_failed") from None
        return True

    async def async_media_play(self) -> None:
        """Send play command to device."""
        if not await self._async_audio_command("play"):
            self.hass.bus.async_fire(SENDDOMAIN, dict(uuid=self.uuidAction, value="play"))
        self.async_schedule_update_ha_state()

    async def async_media_pause(self) -> None:
        """Send pause command to device."""
        if not await self._async_audio_command("pause"):
            self.hass.bus.async_fire(SENDDOMAIN, dict(uuid=self.uuidAction, value="pause"))
        self.async_schedule_update_ha_state()

    async def async_media_stop(self) -> None:
        """Send stop command to device."""
        self.hass.bus.async_fire(SENDDOMAIN, dict(uuid=self.uuidAction, value=audio_zone_stop_value()))
        self.async_schedule_update_ha_state()

    async def async_media_next_track(self) -> None:
        """Send next track command to device."""
        self.hass.bus.async_fire(SENDDOMAIN, dict(uuid=self.uuidAction, value="next"))
        self.async_schedule_update_ha_state()

    async def async_media_previous_track(self) -> None:
        """Send previous track command to device."""
        self.hass.bus.async_fire(SENDDOMAIN, dict(uuid=self.uuidAction, value="prev"))
        self.async_schedule_update_ha_state()

    async def async_set_volume_level(self, volume: float) -> None:
        """Send new volume_level to device."""
        volume_int = int(volume * 100)
        self.hass.bus.async_fire(SENDDOMAIN, dict(uuid=self.uuidAction, value=f"volume/{volume_int}"))
        self.async_schedule_update_ha_state()

    async def async_volume_up(self) -> None:
        """Send volume UP to device."""
        self.hass.bus.async_fire(SENDDOMAIN, dict(uuid=self.uuidAction, value="volUp"))
        self.async_schedule_update_ha_state()

    async def async_volume_down(self) -> None:
        """Send volume DOWN to device."""
        self.hass.bus.async_fire(SENDDOMAIN, dict(uuid=self.uuidAction, value="volDown"))
        self.async_schedule_update_ha_state()