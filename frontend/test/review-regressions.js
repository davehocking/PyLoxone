// Regressions identified by independent OpenAI review. Fake services only.
export async function runReviewRegressions() {
 const check=[];const card=document.createElement('pyloxone-audio-card');const entity='media_player.synthetic';card.setConfig({entity});document.body.append(card);
 const state={state:'idle',attributes:{supported_features:16437,volume_level:.2,media_title:'Synthetic'}};
 let rejectOld;card.hass={states:{[entity]:state},callService:()=>new Promise((_,reject)=>rejectOld=reject)};
 const root=card.shadowRoot;root.querySelector('[data-action=toggle]').click();
 card.setConfig({entity:'media_player.other'});card.hass={states:{'media_player.other':state},callService:async()=>{}};rejectOld(new Error('Synthetic old-entity failure'));await new Promise(r=>setTimeout(r,0));
 check.push({name:'old entity failure not shown on new entity',passed:root.querySelector('.error').textContent===''});
 const volumeCalls=[];const states={'media_player.synthetic':{...state,attributes:{...state.attributes,volume_level:.8}},'media_player.other':state};card.hass={states,callService:async(d,s,p)=>volumeCalls.push({d,s,p})};card.setConfig({entity:'media_player.synthetic'});
 const oldSlider=root.querySelector('input');oldSlider.focus();card.setConfig({entity:'media_player.other'});
 check.push({name:'focused slider resets to newly configured entity',passed:root.querySelector('input').value==='20'});
 oldSlider.value='81';oldSlider.dispatchEvent(new Event('change'));await new Promise(r=>setTimeout(r,0));
 check.push({name:'old volume gesture cannot control new entity',passed:volumeCalls.length===0});
 const freshSlider=root.querySelector('input');freshSlider.value='21';freshSlider.dispatchEvent(new Event('change'));await new Promise(r=>setTimeout(r,0));
 check.push({name:'fresh new-entity volume input uses its own value and target',passed:volumeCalls.at(-1)?.p.entity_id==='media_player.other'&&volumeCalls.at(-1)?.p.volume_level===.21});
 const img=root.querySelector('img');const url='data:image/svg+xml,%3Csvg xmlns="http://www.w3.org/2000/svg"/%3E';img.src=url;img.dispatchEvent(new Event('error'));
 const retry=root.querySelector('[aria-label="Retry artwork"]');check.push({name:'explicit artwork retry offered after error',passed:Boolean(retry&&!retry.hidden)});
 if(retry)retry.click();check.push({name:'explicit retry reassigns the same image without an action replay',passed:Boolean(retry&&retry.hidden&&!img.hidden&&img.getAttribute('src')===url)});
 card.remove();return {checks:check,passed:check.filter(x=>x.passed).length,failed:check.filter(x=>!x.passed).length,household_service_calls:0};
}
