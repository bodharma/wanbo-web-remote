const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

const html = fs.readFileSync(path.join(__dirname, '..', 'web_remote.html'), 'utf8');
const script = html.match(/<script nonce="__TOKEN__">([\s\S]*?)<\/script>/)[1];
const callbacks = new Map(), nodes = new Map(), requests = [];
let kodiResult={state:'media',repaired:false};
function node(id) {
  if (!nodes.has(id)) nodes.set(id, {
    style: {}, dataset: {}, disabled: false,
    setAttribute(name,value){this[name]=value;},
    addEventListener: (event, callback) => callbacks.set(id + ':' + event, callback),
    closest(selector) {
      if (selector==='button') return id.startsWith('button:') ? this : null;
      return id==='#text'||id==='#language' ? this : null;
    },
    blur() {},
  });
  return nodes.get(id);
}
const home = node('button:home'); home.dataset.key = 'home';
home.dataset.i18n='home';
const app = node('button:app'); app.dataset.app = 'jellyfin';app.textContent='Jellyfin';
const intervals=[];
const document = {
    documentElement:{lang:'uk'},
    querySelector: node,
    querySelectorAll: selector => selector==='[data-key]'?[home]:selector==='[data-app]'?[app]:selector==='[data-i18n]'?[home]:[],
    addEventListener: (event, callback) => callbacks.set('document:' + event, callback),
  pointerLockElement:null,exitPointerLock(){this.pointerLockElement=null;},
};
node('#mouse').requestPointerLock=async()=>{document.pointerLockElement=node('#mouse');};
const storage=new Map();
const context=vm.createContext({
  document,TextEncoder,setInterval:callback=>intervals.push(callback),
  navigator:{language:'en-GB'},
  localStorage:{getItem:key=>storage.get(key),setItem:(key,value)=>storage.set(key,value)},
  fetch: async (url, options) => {
    requests.push([url, JSON.parse(options.body)]);
    return { ok: true, blob: async () => ({}), json: async () => kodiResult };
  },
  AbortSignal: { timeout: () => undefined },
  URL: { createObjectURL: () => 'blob:test', revokeObjectURL() {} },
});
vm.runInContext(script,context);
const tick = () => new Promise(resolve => setImmediate(resolve));
function key(code, target) {
  const event = { code, target, repeat: false, prevented: false,
    preventDefault() { this.prevented = true; } };
  callbacks.get('document:keydown')(event);
  return event;
}
(async () => {
  await tick(); requests.length = 0;
  assert.equal(document.documentElement.lang,'en','English browser gets English UI');
  node('#language').value='uk';callbacks.get('#language:change')();
  assert.equal(document.documentElement.lang,'uk');assert.equal(home.textContent,'⌂ Додому');
  node('#text').value='Keep my text';
  node('#language').value='en';callbacks.get('#language:change')();
  assert.equal(home.textContent,'⌂ Home');assert.equal(node('#text').value,'Keep my text');
  assert.equal(storage.get('wanbo-language'),'en');
  const catalogs=JSON.parse(vm.runInContext('JSON.stringify(translations)',context));
  assert.deepEqual(Object.keys(catalogs.en).sort(),Object.keys(catalogs.uk).sort(),'Every translation has both languages');
  for(const match of html.matchAll(/data-i18n(?:-aria|-placeholder)?="([^"]+)"/g)){
    assert.equal(typeof catalogs.en[match[1]],'string',`Missing English ${match[1]}`);
    assert.equal(typeof catalogs.uk[match[1]],'string',`Missing Ukrainian ${match[1]}`);
  }
  assert.equal(key('Enter', home).prevented, false, 'Focused Home keeps native Enter activation');
  assert.equal(requests.length, 0, 'Enter on Home must not send Android OK');
  callbacks.get('button:home:click')({ detail: 0 });
  await tick();
  assert.deepEqual(requests, [['/api/key', {key:'home'}]]);
  requests.length = 0;
  const refresh = node('#refresh'); refresh.closest = () => refresh;
  assert.equal(key('Enter', refresh).prevented, false, 'Focused Refresh keeps native Enter activation');
  assert.equal(requests.length, 0);
  await callbacks.get('#refresh:click')({ currentTarget: refresh });
  assert.deepEqual(requests, [['/api/screenshot', {}]]);
  requests.length = 0;
  assert.equal(key('Enter', node('body')).prevented, true);
  await tick();
  assert.deepEqual(requests, [['/api/key', {key:'ok'}]]);
  requests.length = 0;
  const repeated = {code:'ArrowRight', target:node('body'), repeat:true, preventDefault(){}};
  callbacks.get('document:keydown')(repeated);
  assert.equal(requests.length, 0, 'Held keys must not build a command queue');
  assert.equal(key('ArrowRight',node('#text')).prevented,false,'Typing field keeps navigation keys');
  assert.equal(key('Enter',node('#text')).prevented,false,'Typing field keeps newlines');
  assert.equal(requests.length,0);
  node('#text').value='Привіт, світ 👋';
  await callbacks.get('#textform:submit')({preventDefault(){}});
  assert.deepEqual(requests,[['/api/text',{text:'Привіт, світ 👋'}]]);
  requests.length=0;
  await callbacks.get('#mouse:click')();requests.length=0;
  assert.equal(key('Escape',node('body')).prevented,false,'Esc releases mouse without Android Back');
  callbacks.get('document:mousemove')({movementX:80,movementY:4});
  callbacks.get('document:mousedown')({button:0,preventDefault(){}});
  await callbacks.get('#mouse:click')();
  assert.equal(document.pointerLockElement,node('#mouse'),'Remote click must not release mouse capture');
  callbacks.get('document:pointerlockchange')();
  callbacks.get('document:mousemove')({movementX:20,movementY:2});
  await intervals[0]();await intervals[0]();
  assert.deepEqual(requests,[
    ['/api/mouse',{kind:'click',button:'left',dx:80,dy:4,wheel:0}],
    ['/api/mouse',{kind:'move',dx:20,dy:2,wheel:0}],
  ],'Movement before click stays ordered; later motion follows it');
  document.exitPointerLock();callbacks.get('document:pointerlockchange')();
  requests.length=0;
  await callbacks.get('button:app:click')({detail:0});
  assert.deepEqual(requests,[['/api/app',{app:'jellyfin'}]]);
  requests.length=0;
  assert.equal(typeof callbacks.get('#kodi-check:click'),'function','Kodi state check must be available');
  await callbacks.get('#kodi-check:click')();
  assert.deepEqual(requests,[['/api/kodi',{action:'check'}]]);
  assert.match(node('#status').textContent,/video link/i);
  kodiResult={state:'ready',repaired:true};requests.length=0;
  await callbacks.get('#kodi-repair:click')();
  assert.deepEqual(requests,[['/api/kodi',{action:'repair'}]]);
  assert.match(node('#status').textContent,/restored/i);
  kodiResult={state:'playing',repaired:false};
  await callbacks.get('#kodi-repair:click')();
  assert.match(node('#status').textContent,/stop playback/i);
  assert.equal(node('#kodi-repair').disabled,false);
  node('#language').value='uk';callbacks.get('#language:change')();
  assert.match(node('#status').textContent,/Зупиніть відтворення/);
  console.log('Browser checks passed: keyboard, Unicode form, mouse ordering and Esc.');
})().catch(error => { console.error(error); process.exitCode = 1; });
