// Synthetic rendered-card checks: never load into an authenticated HA page.
import {TRANSLATIONS} from '../pyloxone-audio-card.js';

export async function runExtraLanguageChecks() {
  const checks = [];
  const check = (name, passed) => checks.push({name, passed: Boolean(passed)});
  const entity = 'media_player.synthetic';
  const card = document.createElement('pyloxone-audio-card');
  card.setConfig({entity});
  card.style.cssText = 'display:block;max-width:380px;margin:auto';
  document.body.append(card);
  let calls = 0;
  const state = {state:'playing', attributes:{friendly_name:'Synthetic room', supported_features:16437,
    volume_level:.25, media_title:'原曲 <script>inert</script>', media_artist:'Artista', media_album_name:'Álbum', source:'Local Library'}};
  const set = (language, attrs={}) => {
    card.hass = {locale:{language}, language:'en', states:{[entity]:{...state, attributes:{...state.attributes,...attrs}}},
      callService:async()=>{calls++; throw new Error('Synthetic raw error must never leak');}};
  };
  set('en');
  const root=card.shadowRoot;
  root.querySelector('[aria-label="Expand player"]').click();
  const dialog=root.querySelector('dialog');
  // Establish an actual prior rejection, including for the first locale.
  dialog.querySelector('[data-action="toggle"]').click();
  await new Promise(resolve=>setTimeout(resolve,0));
  if (dialog.querySelector('.error').textContent!==TRANSLATIONS.en.action_failed) {
    throw new Error('Could not establish the synthetic error precondition');
  }
  for (const [locale, code] of [['fr-FR','fr'],['it-IT','it'],['es-ES','es'],['nl-NL','nl'],['pt-PT','pt'],['pt-BR','pt']]) {
    const t=TRANSLATIONS[code];
    check(`${locale} catalogue is present`, Boolean(t));
    if (!t) continue;
    set(locale);
    check(`${locale} open popup survives language change`,dialog.open);
    check(`${locale} compact and popup volume labels`,[...root.querySelectorAll('.volume > span:first-child')].every(x=>x.textContent===t.volume));
    check(`${locale} popup accessible name`,dialog.getAttribute('aria-label')===t.expanded_player);
    check(`${locale} transport accessible labels`,[...root.querySelectorAll('[data-action="toggle"]')].every(x=>x.getAttribute('aria-label')===t.pause));
    check(`${locale} source translated and genuine metadata unchanged`,dialog.querySelector('.source').textContent===t.source_local_library && dialog.querySelector('.track').textContent===state.attributes.media_title && dialog.querySelector('.meta dd').textContent==='Álbum' && !root.querySelector('script'));
    const previousError=dialog.querySelector('.error').textContent;
    const callsBefore=calls;
    dialog.querySelector('[data-action="toggle"]').click();
    const clearedBeforeRejection=[...root.querySelectorAll('.error')].every(x=>x.textContent==='');
    await new Promise(resolve=>setTimeout(resolve,0));
    check(`${locale} fresh action clears old error and sends exactly one request`,previousError===t.action_failed && clearedBeforeRejection && calls===callsBefore+1);
    check(`${locale} error translated without leaking raw message`,dialog.querySelector('.error').textContent===t.action_failed && !root.textContent.includes('Synthetic raw error'));
    set(locale,{audio_event_active:true,audio_event:'fire_alarm'});
    check(`${locale} fire alarm distinct from announcement`,dialog.querySelector('.source').textContent===t.event_fire_alarm && t.event_fire_alarm!==t.event_announcement && dialog.querySelector('.meta dd').hidden);
    check(`${locale} compact and popup have no horizontal overflow`,[...root.querySelectorAll('.player')].every(x=>x.scrollWidth<=x.clientWidth) && dialog.getBoundingClientRect().width<=window.innerWidth);
  }
  set('zz');
  check('unsupported language uses English in existing popup and error',dialog.querySelector('.volume > span').textContent==='Volume' && dialog.querySelector('.error').textContent===TRANSLATIONS.en.action_failed);
  dialog.close(); card.remove();
  return {checks,passed:checks.filter(x=>x.passed).length,failed:checks.filter(x=>!x.passed).length,fake_service_calls:calls,household_service_calls:0};
}
