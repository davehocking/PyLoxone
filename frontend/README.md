# Optional PyLoxone Audio card

Show track information and artwork on a Home Assistant dashboard, with a larger
player when you open the card. You can use this alongside Home Assistant's
built-in media card; it does not replace it.

## What it shows

- Track title, artist, artwork and the current source.
- An album name when one is available. Otherwise, the album row is left out.
- Doorbells, spoken messages, announcements and alarms while they play in the room.

Play, pause, volume and supported track-skip controls are available. Previous and
next buttons are hidden when they are not supported, including for Loxone Radio.
The card controls the current selection; it does not let you choose another source
or browse for music. If an action fails, the card shows an error.

The card follows your Home Assistant language setting. English, German, Czech,
French, Italian, Spanish, Dutch and Portuguese are included; other languages use
English. Song and album information is not translated.

## Installation

First, set up [Audioserver audio support](../README.md#optional-audioserver-audio-support-experimental)
in PyLoxone if you want track information.

1. Copy `pyloxone-audio-card.js` into Home Assistant's `/config/www/` folder.
2. Add `/local/pyloxone-audio-card.js?v=1` as a dashboard resource of type
   **JavaScript module**.
3. Add a manual card to your dashboard:

```yaml
type: custom:pyloxone-audio-card
entity: media_player.example
# Optional display name:
name: Room Audio
```

Replace `media_player.example` with your existing Loxone audio player's entity ID.

When updating the card, replace the JS file and increase the number after `?v=`
in the resource address, then refresh the page. This makes the browser load the
new file. You do not need to restart Home Assistant if it already serves files
from `/config/www/`.

To remove the card, delete it from your dashboard. If no other card uses the
resource, you can also remove that resource and its JS file.

## For contributors

Implementation details and test instructions are in the
[developer notes](../docs/audioserver-development.md#custom-card-development).
