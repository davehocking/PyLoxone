// Actual custom-element localisation checks. Synthetic entities/fake services only.
export async function runLocalisationChecks() {
  const checks = [];
  const check = (name, passed) => checks.push({name, passed: Boolean(passed)});
  const entity = 'media_player.synthetic';
  const card = document.createElement('pyloxone-audio-card');
  card.setConfig({entity});
  card.style.cssText = 'display:block;max-width:380px;margin:auto';
  document.body.append(card);
  let calls = 0;
  const state = {state: 'playing', attributes: {
    friendly_name: 'Synthetic room', supported_features: 16437, volume_level: .25,
    media_title: '原曲 <script>inert</script>', media_artist: 'Artist', source: 'Local Library',
  }};
  const set = (locale, language, attributes = {}) => {
    card.hass = {locale, language, states: {[entity]: {...state, attributes: {...state.attributes, ...attributes}}},
      callService: async () => { calls++; throw new Error('Synthetic raw error must not leak'); }};
  };
  set({language: 'de-DE'}, 'en');
  const root = card.shadowRoot;
  check('HA locale precedes legacy language', root.querySelector('.volume > span').textContent === 'Lautstärke');
  check('state/source translated without changing genuine metadata',
    root.querySelector('.status').textContent === 'Wiedergabe' &&
    root.querySelector('.source').textContent === 'Lokale Bibliothek' &&
    root.querySelector('.track').textContent === state.attributes.media_title && !root.querySelector('.track script'));
  check('missing album absent in both surfaces', [...root.querySelectorAll('.meta dd:first-of-type')].every(x => x.hidden));
  root.querySelector('[aria-label="Player erweitern"]').click();
  const dialog = root.querySelector('dialog');
  check('German expanded accessible name', dialog.open && dialog.getAttribute('aria-label') === 'Erweiterter Audio-Player');
  set({language: 'cs-CZ'}, 'en');
  check('language switches in-place in open popup', dialog.open && dialog.querySelector('.volume > span').textContent === 'Hlasitost');
  check('Czech transport accessibility label', dialog.querySelector('[data-action="toggle"]').getAttribute('aria-label') === 'Pozastavit');
  check('Czech generic source translation', dialog.querySelector('.source').textContent === 'Místní knihovna');
  dialog.querySelector('[data-action="toggle"]').click();
  await new Promise(resolve => setTimeout(resolve, 0));
  check('localised failure never reveals raw service error', calls === 1 && dialog.querySelector('.error').textContent.includes('nemohl dokončit') && !root.textContent.includes('Synthetic raw error'));
  set(undefined, 'de');
  check('legacy HA language used and existing error retranslated', root.querySelector('.volume > span').textContent === 'Lautstärke' && root.querySelector('.error').textContent.includes('konnte diese Aktion nicht abschließen'));
  set({language: 'zz'}, 'de');
  check('unsupported language falls back to English', dialog.querySelector('.volume > span').textContent === 'Volume');
  set({language: 'cs'}, 'en', {media_album_name: '本物のアルバム'});
  check('genuine Unicode album appears unchanged', !dialog.querySelector('.meta dd').hidden && dialog.querySelector('.meta dd').textContent === '本物のアルバム');
  set({language: 'de'}, 'en', {audio_event_active: true, audio_event: 'fire_alarm', media_album_name: 'Stale'});
  check('accurate translated fire alarm rather than generic announcement', dialog.querySelector('.source').textContent === 'Feueralarm' && dialog.querySelector('.meta dd').hidden);
  check('translated compact and popup layouts fit', [...root.querySelectorAll('.player')].every(x => x.scrollWidth <= x.clientWidth));
  dialog.close();
  card.remove();
  return {checks, passed: checks.filter(x => x.passed).length, failed: checks.filter(x => !x.passed).length, fake_service_calls: calls, household_service_calls: 0};
}
