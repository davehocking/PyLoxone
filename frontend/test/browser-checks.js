// Browser acceptance tests. Fake HA services only; never send household commands.
export async function runBrowserChecks() {
 const passed=[];const check=(name,condition)=>{if(!condition)throw new Error(name);passed.push(name);};
 const entity='media_player.synthetic',calls=[];
 const card=document.createElement('pyloxone-audio-card');card.setConfig({entity});card.style.cssText='display:block;max-width:380px;margin:auto';document.body.append(card);
 const state=(attributes={},value='idle')=>({state:value,attributes:{friendly_name:'Test Audio',supported_features:16437,volume_level:.25,...attributes}});
 let active=state({media_title:'Track',media_artist:'Artist',media_album_name:'Album',source:'Spotify'});
 const service=async(domain,name,data)=>calls.push({domain,name,data});
 const set=(s,fn=service)=>{active=s;card.hass={states:{[entity]:s},callService:fn};};
 const tick=()=>new Promise(resolve=>setTimeout(resolve,0));
 set(active);const root=card.shadowRoot;
 check('visible album and source',root.querySelector('.meta').textContent.includes('Album')&&root.querySelector('.source').textContent==='Spotify');
 root.querySelector('[data-action=toggle]').click();await tick();check('play exact HA route',calls.at(-1).name==='media_play'&&calls.at(-1).data.entity_id===entity);
 check('no optimistic state mutation',active.state==='idle');
 set(state({},'playing'));root.querySelector('[data-action=toggle]').click();await tick();check('pause exact HA route',calls.at(-1).name==='media_pause');
 root.querySelector('[data-action=previous]').click();await tick();root.querySelector('[data-action=next]').click();await tick();check('native skip routes',calls.at(-2).name==='media_previous_track'&&calls.at(-1).name==='media_next_track');
 const input=root.querySelector('input');input.value='37';input.dispatchEvent(new Event('change'));await tick();check('volume fraction exact',calls.at(-1).name==='volume_set'&&calls.at(-1).data.volume_level===.37);
 let finish;const n=calls.length;set(state(),async(d,s,p)=>{calls.push({domain:d,name:s,data:p});await new Promise(r=>finish=r);});
 root.querySelector('[data-action=toggle]').click();root.querySelector('[data-action=toggle]').click();check('one in-flight request',calls.length===n+1&&root.querySelector('[data-action=toggle]').disabled);finish();await tick();
 const before=calls.length;set(state(),async()=>{calls.push({failed:true});throw new Error('synthetic failure');});root.querySelector('[data-action=toggle]').click();await tick();check('failure no retry/fallback',calls.length===before+1&&root.querySelector('.error').textContent.includes('No retry'));
 set(state({media_title:'<img src=x onerror=alert(1)>',media_artist:'<script>bad</script>',media_album_name:'[link](https://example.invalid)'}));
 check('metadata is inert text',root.querySelector('.track').textContent.startsWith('<img')&&!root.querySelector('.track img')&&!root.querySelector('.artist script'));
 set(state({source:'Local Library',media_folder:'Synthetic folder'}));check('absent album row is omitted; folder is ignored',root.querySelector('.meta dt').hidden&&root.querySelector('.meta dd').hidden&&root.querySelector('.meta dd').textContent==='');
 set(state({source:'Local Library',media_folder:'Fallback',media_album_name:'Real Album'}));check('genuine album is shown',!root.querySelector('.meta dd').hidden&&root.querySelector('.meta dd').textContent==='Real Album');
 set(state({source:'Spotify',media_album_name:'Stale'},'unavailable'));check('unavailable clears metadata/disables controls',root.querySelector('.source').textContent==='Not reported'&&root.querySelector('.meta dd').hidden&&input.disabled);
 set(state({source:'Spotify',media_album_name:'Old',audio_event_active:true}));check('event clears old source/album',root.querySelector('.source').textContent==='Audio interruption'&&root.querySelector('.meta dd').hidden);
 set(state({supported_features:0}));check('unsupported controls disabled',[...root.querySelectorAll('.transport,input')].every(x=>x.disabled));
 set(state({volume_level:null}));check('missing volume not invented',input.disabled&&root.querySelector('.percent').textContent==='—');
 set(state({source:'AirPlay',media_album_name:'Album'}));const expand=root.querySelector('[aria-label="Expand player"]');expand.focus();expand.click();const dialog=root.querySelector('dialog');
 check('expanded popup opens',dialog.open&&dialog.querySelector('.source').textContent==='AirPlay');
 set(state({source:'Loxone Radio',media_album_name:'Updated'}));check('open popup updates in place',dialog.open&&dialog.querySelector('.source').textContent==='Loxone Radio'&&dialog.querySelector('.meta dd').textContent==='Updated');
 dialog.querySelector('[aria-label="Close player"]').click();check('popup closes',!dialog.open);
 check('mobile card no horizontal overflow',root.querySelector('.player').scrollWidth<=root.querySelector('.player').clientWidth);
 check('custom card picker registered',window.customCards.some(x=>x.type==='pyloxone-audio-card'));
 card.remove();return {passed:passed.length,checks:passed,household_service_calls:0,fake_service_calls:calls.length};
}
