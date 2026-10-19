import { test } from 'node:test';
import assert from 'node:assert/strict';
import * as card from '../pyloxone-audio-card.js';

const state = attributes => ({ state: 'playing', attributes });

test('normalises regional/underscore locale tags with English fallback', () => {
  assert.equal(card.localize('de-DE', 'volume'), 'Lautstärke');
  assert.equal(card.localize('cs_CZ', 'volume'), 'Hlasitost');
  for (const locale of ['zz', '', null, {}, '__proto__']) {
    assert.equal(card.localize(locale, 'volume'), 'Volume');
  }
  assert.equal(card.localize('en-GB', 'play'), 'Play');
  assert.equal(card.localize('de', 'missing_key'), 'missing_key');
  assert.equal(card.localize('de', '__proto__'), '__proto__');
  assert.equal(card.localize('de', null), '');
});

test('missing title/source and source labels follow selected language, not metadata text', () => {
  assert.notEqual(card.metadataView(state({}), 'de').title, 'No track information');
  assert.notEqual(card.metadataView(state({}), 'cs').source, 'Not reported');
  const view = card.metadataView(state({ media_title: '音楽', media_artist: 'Künstler', media_album_name: 'Álbum', source: 'Local Library' }), 'de');
  assert.equal(view.title, '音楽');
  assert.equal(view.artist, 'Künstler');
  assert.equal(view.album, 'Álbum');
  assert.notEqual(view.source, 'Local Library');
  assert.equal(card.metadataView(state({ source: 'Future reported source' }), 'cs').source, 'Future reported source');
});

test('interruption labels reflect the reported event, not always announcement', () => {
  assert.equal(card.metadataView(state({ audio_event_active: true, audio_event: 'doorbell' })).source, 'Doorbell');
  assert.equal(card.metadataView(state({ audio_event_active: true, audio_event: 'fire_alarm' })).source, 'Fire alarm');
  for (const event of ['event_999', '__proto__', null]) {
    assert.equal(card.metadataView(state({ audio_event_active: true, audio_event: event })).source, 'Audio interruption');
  }
});

test('validation errors can be presented in the selected language', () => {
  assert.throws(() => card.controlRequest('light.example', 'play', undefined, 'de'), /media_player/);
  assert.throws(() => card.controlRequest('media_player.example', 'stop', undefined, 'cs'), error => error.message !== 'Unsupported player action');
  assert.throws(() => card.controlRequest('media_player.example', 'volume', Infinity, 'de'), error => error.message.includes('0') && error.message.includes('1'));
});
