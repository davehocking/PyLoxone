// Source capability transitions; synthetic HA state/services only.
export async function runRadioChecks() {
  const checks = [];
  const check = (name, ok) => { if (!ok) throw new Error(name); checks.push(name); };
  const entity = 'media_player.synthetic_radio';
  const card = document.createElement('pyloxone-audio-card');
  card.setConfig({entity});
  document.body.append(card);
  const calls = [];
  const set = (features, source = 'Loxone Radio', state = 'playing') => {
    card.hass = {states: {[entity]: {state, attributes: {
      supported_features: features, source, volume_level: 0.25,
    }}}, callService: async (...args) => calls.push(args)};
  };
  const skips = () => [...card.shadowRoot.querySelectorAll('[data-action=previous],[data-action=next]')];
  const visible = b => !b.hidden && getComputedStyle(b).display !== 'none';
  try {
    set(16389);
    check('radio skips hidden on compact and expanded surfaces', skips().length === 4 && skips().every(b => b.hidden));
    card.shadowRoot.querySelector('[aria-label="Expand player"]').click();
    check('open radio popup has no skip icons', card.shadowRoot.querySelector('dialog').open && skips().every(b => !visible(b)));
    check('radio play/pause and volume remain enabled', [...card.shadowRoot.querySelectorAll('[data-action=toggle],input')].every(b => !b.disabled));
    for (const b of skips()) b.click();
    await Promise.resolve();
    check('hidden unsupported skips cannot call services', calls.length === 0);
    set(16437, 'Spotify');
    check('source change restores compact and expanded skip icons', skips().every(b => !b.hidden));
    set(16389, 'Loxone Radio');
    check('return to radio hides skips in already open popup', skips().every(b => b.hidden));
    set(16405, 'Synthetic provider');
    check('independent previous/next flags are respected', skips().filter(b => b.dataset.action === 'previous').every(b => !b.hidden) && skips().filter(b => b.dataset.action === 'next').every(b => b.hidden));
    set(16437, 'Sonos-provided source');
    check('other provider retains advertised controls', skips().every(b => !b.hidden));
    set(0, 'Unknown');
    check('missing capabilities have no skip icons', skips().every(b => b.hidden));
    return {passed: checks.length, checks, household_service_calls: 0, fake_service_calls: calls.length};
  } finally { card.remove(); }
}
