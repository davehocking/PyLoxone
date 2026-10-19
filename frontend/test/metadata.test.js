import { test } from 'node:test';
import assert from 'node:assert/strict';
import { metadataView, controlRequest, safeArtwork } from '../pyloxone-audio-card.js';

const entity = 'media_player.example';
const state = (attributes = {}, value = 'paused') => ({state: value, attributes});
test('album and source remain separate from genuine artist/title', () => {
  const v = metadataView(state({media_title:'Track', media_artist:'Artist', media_album_name:'Album', source:'Spotify'}));
  assert.equal(v.title,'Track'); assert.equal(v.artist,'Artist'); assert.equal(v.album,'Album'); assert.equal(v.source,'Spotify');
});
test('missing albums stay absent; folder data is deliberately ignored', () => {
  assert.equal(metadataView(state({media_folder:'Folder', source:'Local Library'})).album,'');
  assert.equal(metadataView(state({media_folder:'Folder',media_album_name:'Real',source:'Local Library'})).album,'Real');
});
for (const source of ['Spotify','Loxone Radio','AirPlay','Local Library','TuneIn','Line-In']) {
  test(`preserves exact reported source ${source}`, () => assert.equal(metadataView(state({source})).source,source));
}
test('does not guess a local source or folder for unidentified streams', () => {
  const v=metadataView(state({media_folder:'Not evidence',media_album_name:'Album'}));
  assert.equal(v.source,'Not reported'); assert.equal(v.album,'Album');
  assert.equal(metadataView(state({media_folder:'Not evidence'})).album,'');
});
test('unknown/unavailable hide stale track, source, folder and artwork', () => {
  for(const s of ['unknown','unavailable']) {
    const v=metadataView(state({media_title:'stale',media_artist:'old',media_album_name:'old',source:'Spotify'},s));
    assert.equal(v.available,false);assert.equal(v.album,'');assert.equal(v.source,'Not reported');assert.equal(v.artist,'');
  }
  assert.equal(metadataView(null).available,false);
});
test('interruptions do not show stale album/source/folder', () => {
  const v=metadataView(state({audio_event_active:true,audio_event:'doorbell',media_album_name:'Old',source:'Spotify',media_folder:'Old'}));
  assert.equal(v.album,'');assert.equal(v.source,'Doorbell');
});
test('invalid field types do not become text or a false album', () => {
  const v=metadataView(state({media_title:[],media_artist:{},media_album_name:3,source:[],media_folder:false}));
  assert.equal(v.title,'No track information');assert.equal(v.artist,'');assert.equal(v.album,'');assert.equal(v.source,'Not reported');
});
for (const [action, service] of [['play','media_play'],['pause','media_pause'],['previous','media_previous_track'],['next','media_next_track']]) {
  test(`routes ${action} only through HA's exact entity ${service}`, () => {
    assert.deepEqual(controlRequest(entity,action),{domain:'media_player',service,data:{entity_id:entity}});
  });
}
test('volume accepts finite fractions only, including zero', () => {
  for(const v of [0,0.37,1]) assert.deepEqual(controlRequest(entity,'volume',v).data,{entity_id:entity,volume_level:v});
  for(const v of [-1,1.1,NaN,Infinity,'0.3',null]) assert.throws(()=>controlRequest(entity,'volume',v));
});
test('no arbitrary services, targets or source-changing fallback', () => {
  for(const target of ['light.example','media_player.x/../../','media_player.example,media_player.other']) assert.throws(()=>controlRequest(target,'play'));
  for(const action of ['play_media','tts','select_source','resume','stop']) assert.throws(()=>controlRequest(entity,action));
});
test('artwork is same-origin HTTP(S) only; no credentialed/external/javascript URL', () => {
  const base='https://ha.example';
  assert.equal(safeArtwork('/api/media_player_proxy/media_player.example?token=synthetic',base),base+'/api/media_player_proxy/media_player.example?token=synthetic');
  for(const p of ['https://other.example/a','javascript:alert(1)','data:text/html,hi','//other.example/a','https://user:pass@ha.example/a',null,42]) assert.equal(safeArtwork(p,base),null);
});
