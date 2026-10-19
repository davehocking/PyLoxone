import { test } from 'node:test';
import assert from 'node:assert/strict';
import {TRANSLATIONS, localize, metadataView, controlRequest} from '../pyloxone-audio-card.js';

const languages = ['fr', 'it', 'es', 'nl', 'pt'];
const sentinels = {fr: 'Lire', it: 'Riproduci', es: 'Reproducir', nl: 'Afspelen', pt: 'Reproduzir'};

for (const language of languages) {
  test(`${language} supplies every card key without relying on English`, () => {
    assert.ok(Object.hasOwn(TRANSLATIONS, language));
    const catalogue = TRANSLATIONS[language];
    assert.deepEqual(Object.keys(catalogue).sort(), Object.keys(TRANSLATIONS.en).sort());
    assert.equal(localize(language, 'play'), sentinels[language]);
    for (const [key, value] of Object.entries(catalogue)) {
      assert.equal(typeof value, 'string'); assert.ok(value.trim());
      assert.equal(localize(language, key), value);
      assert.deepEqual(value.match(/\{[^{}]+\}/g), TRANSLATIONS.en[key].match(/\{[^{}]+\}/g));
    }
    assert.notEqual(catalogue.event_fire_alarm, catalogue.event_announcement);
    assert.ok(catalogue.invalid_entity.includes('media_player'));
    const state = {state:'playing', attributes:{media_title:'原曲', media_artist:'Artista', media_album_name:'Álbum', source:'Spotify'}};
    const view = metadataView(state, language);
    assert.equal(view.title, '原曲'); assert.equal(view.artist, 'Artista'); assert.equal(view.album, 'Álbum'); assert.equal(view.source, 'Spotify');
    assert.throws(() => controlRequest('light.example','play',undefined,language), error => error.message === catalogue.invalid_entity);
    assert.deepEqual(controlRequest('media_player.example','play',undefined,language), {domain:'media_player',service:'media_play',data:{entity_id:'media_player.example'}});
  });
}

test('new regional settings use their base catalogue, including Portuguese variants', () => {
  for (const [locale, language] of [['fr-FR','fr'],['fr_CA','fr'],['it-IT','it'],['es-ES','es'],['es-419','es'],['nl-NL','nl'],['nl_BE','nl'],['pt-PT','pt'],['pt_BR','pt']]) {
    for (const key of Object.keys(TRANSLATIONS.en)) assert.equal(localize(locale,key), TRANSLATIONS[language]?.[key], `${locale}/${key}`);
  }
});

test('unsupported languages and invalid keys still have safe fallback', () => {
  for (const locale of ['zz', null, {}, '__proto__']) assert.equal(localize(locale,'missing_title'),TRANSLATIONS.en.missing_title);
  assert.equal(localize('fr','__proto__'),'__proto__');
  assert.equal(localize('pt',null),'');
});
