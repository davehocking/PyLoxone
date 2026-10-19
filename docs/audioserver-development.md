# Audioserver development notes

This page contains implementation details and test guidance for contributors.
For setup and everyday use, see the [audio support guide](../README.md#optional-audioserver-audio-support-experimental).

The details below describe the current experimental implementation. They are
not a promise of compatibility with every Audio Server firmware or music service.

Existing installations remain Miniserver-only by default. Two separate options
extend the existing `AudioZoneV2` entities; neither creates another service.

### Passive metadata and interruption telemetry

Enter a **bare Audioserver hostname or IP address** in **Audioserver host** in the
integration's setup/options form. Do not include a URL, port, path or credentials.
IPv4/IPv6 are accepted. Leave it blank to disable the optional listener; if
player playback is enabled, turn that option off in the same save. Saving
options reloads the integration and closes the previous listener.

- One entry-owned receive-only `remotecontrol` WebSocket listens on port `7091`.
  It sends no authentication or playback commands. The event connection is
  unencrypted: use a trusted local network. Environment proxies are bypassed.
- Player mapping uses only `details.playerid` in the Miniserver structure file,
  never names, room order or guessed identities. One explicitly configured
  Audioserver per integration entry is supported; there is no discovery.
- Existing players gain genuine title, artist, album, source and artwork when
  supplied. Spotify, Loxone Radio, AirPlay, Local Library, TuneIn and Line-In are
  distinct. A nonempty `library:local:track:` path identifies Local Library, not
  its folder. Missing albums are left absent: no album recovery, folder fallback,
  library crawl or index lookup is implemented.
- A qualified music-content-type hint permits the native HA artist subtitle.
  Events, unsupported/unknown snapshots and disconnect clear stale hints/metadata.
  Relative artwork resolves against the configured Audioserver. Optional metadata
  failure does not change native player availability.
- Disconnect invalidates optional state and reconnect uses capped backoff.

The existing player exposes `audio_event` (`doorbell`, `alarm_clock`, `tts`,
`custom_sound`, `announcement`, `alarm`, `fire_alarm`, or `event_<code>`) and
`audio_event_active`. Activity is `true` only for an evidenced event with that
zone's `mode: "play"`; known inactivity is `false`, and missing/invalid activity
or disconnect is `null`. Unmapped/opted-out players gain neither attribute.

Explicit `eventype` takes precedence. The issue contributor's alternate AirPlay
announcement shape is recognised only for integer `type: 7` with exact
`audiopath: "airplay"` when `eventype` is absent, null or integer zero. Other `type` values are **not**
generic event codes. Stopped broadcast recipients are not marked active and do
not display repeated TTS as music. These cases include synthetic reconstructions,
not independent recordings from every firmware version.

Alarm/fire-alarm/alarm-clock endings alone are held for **1.5 seconds without
renewed event playback** to suppress measured flaps. Other endings are immediate.
Timers affect event attributes only; replacement/disconnect/reload/unload cancels
pending timers. Unchanged artwork is not invalidated by an event-only timer.
A transition to `null` is not an observed completion. This is experimental audio
interruption telemetry, **not a safety-rated alarm detector or audibility proof**.

### Optional player-scoped play/pause

**Use Audio Server player Play/Pause** is separately opt-in and requires a configured
host. When disabled, existing Miniserver controls remain unchanged. When enabled,
play/pause use the exact Audio Server player route and the integration's existing
Miniserver authentication token, instead of room-start/favourite commands.
Volume, skip, availability and native playback-state feedback remain Miniserver
owned; this does not add browsing, searching, source selection or seeking.

The command connection is authenticated, serialized and bounded. It follows the
native RSA exchange over unencrypted `ws://`; the server public key is not
independently authenticated. An active LAN interceptor could substitute that key
and recover the token, so enable this only on a trusted local network, not an
untrusted network or an Internet-exposed endpoint. This is not TLS server-identity
verification.

Acknowledgements are matched by literal response key/value within a wall-clock
deadline, not an
arbitrary number of unsolicited event frames. A failed, missing or ambiguous
acknowledgement raises an HA error: **no automatic retry and no room-start or
source-changing fallback**. Owned connections close on unload. This route is
intended to preserve the selected source, but physical source/queue/position and
retained-volume acceptance must be checked separately on the intended hardware.

### Read-only telemetry versus playback controls

**Read-only means listening, not controlling.** The optional metadata/interruption
listener observes what the Audio Server reports; it never starts, stops, skips,
changes volume or selects a source. Existing media-player controls remain separate.
When you deliberately use a control, HA sends a command: those actions are not
read-only. The separately enabled player-scoped option changes the route for play
and pause only, as described above.

The current scope is **observe the current source and control its playback**, not
choose new content. Play/pause, stop (implemented as pause), volume and applicable
track skipping are available; source selection, choosing a new station/playlist,
media browsing/search and seeking within a track are not added. Previous/next
means tracks where supported, not rewind/fast-forward. A live radio stream cannot
resume audio from the exact paused instant without provider-side buffering.

When metadata explicitly identifies **Loxone Radio**, the entity stops advertising
previous/next-track capabilities because those controls do not apply to that
stream. HA's standard media-control card and more-info dialog use these native
capabilities; no HA frontend patch is required. The optional custom card also
hides unsupported skip buttons, in both compact and expanded views. Other sources
retain existing advertised controls. Missing telemetry, disconnection or opting
out preserves the original Miniserver feature baseline rather than guessing a
source or disabling controls for every installation.

Playback can reapply a device's configured startup/default volume. This
contribution does not change that policy, silently restore volume or claim to fix
existing volume quantisation/control behaviour. Validate the intended source and
room before relying on playback continuity; a successful acknowledgement is not
physical source/queue/position or retained-volume proof.

### Presentation, translations and validation

HA's native dialog remains unchanged and can be used on its own. The alternative
card is optional: both presentations use the same existing media-player entities,
so users can choose either or both without installing a replacement global UI.
The dependency-free dashboard card and expanded player can show supplied albums
and a separate source label; see
[frontend/README.md](../frontend/README.md). Missing album rows are omitted. The card
uses existing exact-entity HA services and never accesses a device directly.

Setup/options labels, validation errors and playback exceptions have English,
German, Czech, French, Italian, Spanish, Dutch and Portuguese translations. The
optional card follows the user's HA language with the same languages and English
fallback, including accessibility labels,
errors and interruption names. Protocol values/brand names and genuine Unicode
media metadata are not translated or restricted to those languages. HA provides
the translation framework; custom integration/card strings must still be supplied.

Offline tests use real HA entity/lifecycle/config/translation code with fake
transports, local AF_UNIX WebSocket framing, original synthetic fixtures and
SebastianEitzert's sanitised samples. Provenance is documented in
`tests/fixtures/audioserver/README.md`. Browser tests exercise the actual custom
element with synthetic entities/fake services. They do not establish audible
playback, native firmware compatibility or an owner's matching rendered cover.
Run `./scripts/test -q --disable-socket --allow-unix-socket` and `cd frontend && npm test`.
The audio workflow runs Python and frontend unit tests; browser checks are separate.

Browse/play-media/search/seek/Spotify hand-over from the broader follow-up request
remain separate work. Do not infer that all of issue #525 is implemented.


## Custom card development

# Optional PyLoxone Audio card

A dependency-free Home Assistant dashboard card with a larger expanded player.
It does **not** replace or patch HA's built-in media-player more-info dialog.
No extra service, device connection, dependency or polling is used.

## Presentation

- Genuine artwork/title/artist, a supplied **Album** and a separate **Source**.
- An absent album row is omitted. Missing-album recovery and containing-folder
  fallbacks were deliberately dropped; `media_folder` is ignored. No library
  crawl, arbitrary-ID decoding, index query or invented album is performed.
- Source comes from the entity, never a guess from title/artwork/album/room.
  Spotify, Loxone Radio, AirPlay, Local Library, TuneIn and Line-In stay distinct.
  Unknown source is labelled Not reported in the chosen language.
- Actual interruption names (including fire alarm/doorbell) replace the source
  during an active event; stale album information is hidden. Unknown event codes
  use a generic Audio interruption label, never a false announcement diagnosis.
- Same-origin HTTP(S) HA artwork only. Failed artwork offers an explicit image-only
  retry, not a playback retry. Compact/expanded surfaces share reported HA state.

## Localisation

The card follows `hass.locale.language`, falling back to `hass.language` on older
HA versions. English, German, Czech, French, Italian, Spanish, Dutch and Portuguese
labels include states, controls, source presentation, accessible names,
interruptions and errors. Regional tags such as `de-DE`, `fr-FR` and `pt-BR` use
their base language; unsupported languages fall back to English. Portuguese uses
one shared catalogue; the integration also supplies exact `pt-PT` and `pt-BR`
files so HA's loader can resolve either regional setting. These are aliases of
the same Portuguese wording, not separate dialect translations.
Open popups and existing errors update in-place when the user's language changes.
Media metadata and service identifiers are preserved, not translated. Brand names
stay intact. More languages can be added to the module's catalogue without changing
backend protocol code. These card catalogues are separate from the integration's
standard HA `translations/<language>.json` files.

## Controls

Only existing exact-entity HA services are called: `media_play`, `media_pause`,
`media_previous_track`, `media_next_track`, `volume_set`. Feature flags/availability
gate controls; volume is a finite 0–1 fraction. Unsupported previous/next buttons
are hidden on both compact and expanded surfaces, not merely greyed out. The
integration removes those two capabilities when the reported source is Loxone
Radio; HA's built-in card/dialog can use the same source-aware capabilities.
The card does not guess capability from a room, track title or provider name.
One request may be in flight.
Errors are generic, localised and never retried or redirected. No optimistic state
mutation, direct device credentials or test sounds are part of installation.

## Installation

Copy `pyloxone-audio-card.js` into `/config/www/`, add a JavaScript **module**
resource `/local/pyloxone-audio-card.js?v=<content-version>`, and configure:

```yaml
type: custom:pyloxone-audio-card
entity: media_player.example
# Optional presentation-only name:
name: Room Audio
```

Use an exact existing entity. No Core restart is needed when the existing `www`
route serves modules. Change the content-version when updating the single module.
Remove only the added card/dashboard and exact resource to reverse installation;
remove its module only if unused. Preserve other dashboards/options/resources.
Backend changes require their own separately validated deployment.

## Tests

Run `npm test` (Node's built-in runner, no packages). The audio CI workflow also
runs these unit tests. Browser modules `test/browser-checks.js`,
`test/review-regressions.js`, `test/localisation-browser.js` and
`test/radio-browser.js` must be imported
in a blank test page after loading the card; invoke their exported functions.
They use synthetic entities/fake services only. Never attach them to a live HA
card. Run at actual mobile/desktop CSS viewport widths. They cover payloads,
modal updates, localisation/a11y, inert text, unavailable/event data, missing
albums, features, volume, request isolation, artwork retry and reconfiguration.
These are not household audibility/source/queue/position/volume or owner-phone
rendering tests. The module inherits the repository's Apache-2.0 licence.
