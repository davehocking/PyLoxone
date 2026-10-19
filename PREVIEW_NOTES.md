# PyLoxone Audio — experimental test preview

Based on PyLoxone 0.10.1. This is a preview for testing and feedback, not an
official release. The original licence, notices and sample credits are included.

## Included

- Track title, artist, artwork and source information for AudioZoneV2 players.
- Information about doorbells, spoken messages, announcements and alarms.
- Optional Audio Server play/pause for the current selection.
- The built-in Home Assistant media card and an optional custom card.
  Unsupported previous/next buttons are hidden, including for Loxone Radio.
- English, German, Czech, French, Italian, Spanish, Dutch and Portuguese labels
  in the integration and custom card. Portuguese uses shared wording for
  Portugal and Brazil. Song information is not translated.

There is no new source selection, music browsing, search, seeking or Spotify
handover. This does not complete all of issue #525.

## Trying the preview

Back up Home Assistant and your existing `custom_components/loxone` directory
before installing. Keep your existing PyLoxone configuration. Follow the audio
setup in `README.md`; the optional custom card has its own `frontend/README.md`.
You can test track information without enabling the separate play/pause option.

Use only a trusted home network. The Audio Server connection is not encrypted.
With play/pause enabled, someone intercepting it could steal the login token.
Do not expose port `7091` to the internet.

## Known limitations

Play/pause may reset volume to the room's default, including in the Loxone app.
This preview does not change that setting. Live radio may not resume from the
point where it was paused. Track information may stay empty after a restart
until playback changes. Alarm information does not replace an alarm or
fire-safety system.

## Checks

560 offline Python tests, 30 Node tests and 109 synthetic browser checks at each
360px and 1280px width passed. This includes Home Assistant's actual translation
loader and both compact and expanded custom-card views. The Python suite has
35 inherited warnings; no new lint diagnostics were introduced. Independent
code and translation review found no blocking issue.

This preview is deployed on one home installation with Home Assistant 2026.10.2
and PyLoxone 0.10.1. The live translation resources and served card were checked;
existing integration settings and dashboards were preserved. These checks do
not prove physical playback, phone rendering or compatibility with every
firmware and music provider. Native-speaker wording feedback is welcome.
Contributor details and test instructions are in `docs/audioserver-development.md`.
