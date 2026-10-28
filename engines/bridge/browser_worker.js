const host = chrome.runtime.connectNative('local.alisa_voice');
const attached = new Set();
async function target(tabId) {
  const tab = await chrome.tabs.get(Number(tabId));
  if (new URL(tab.url).origin !== 'https://chatgpt.com') throw new Error('Unexpected browser origin');
  return tab;
}
async function attach(tabId) {
  tabId = Number(tabId); await target(tabId);
  if (attached.has(tabId)) return;
  // Let the ordinary page complete its browser check before attaching the
  // owner-installed debugger extension. No page checks are clicked or bypassed.
  const deadline = Date.now() + 30000;
  while (Date.now() < deadline) {
    const values = await chrome.scripting.executeScript({target:{tabId},func:() => {
      const blocked = /just a moment|один момент|verify you are human|checking your browser/i.test(document.title);
      return !blocked && document.querySelectorAll('button').length > 0;
    }});
    if (values[0]?.result) break;
    await new Promise(resolve=>setTimeout(resolve,500));
  }
  await chrome.debugger.attach({tabId}, '1.3'); attached.add(tabId);
}
async function execute(request) {
  const params = request.params || {};
  if (request.method === 'Target.getTargets') {
    const tabs = await chrome.tabs.query({url:'https://chatgpt.com/*'});
    return {targetInfos:tabs.map(t=>({type:'page',targetId:String(t.id),url:t.url}))};
  }
  if (request.method === 'Target.attachToTarget') {
    await attach(params.targetId); return {sessionId:String(params.targetId)};
  }
  if (request.method === 'Browser.grantPermissions') {
    await chrome.contentSettings.microphone.set({primaryPattern:'https://chatgpt.com/*',setting:'allow',scope:'regular'});
    return {};
  }
  const tabId = Number(request.sessionId);
  if (!Number.isInteger(tabId)) throw new Error('Missing dedicated tab');
  await target(tabId);
  const allowed = new Set(['Runtime.evaluate','Page.navigate','Input.dispatchMouseEvent']);
  if (!allowed.has(request.method)) throw new Error('Unsupported browser operation');
  if (request.method === 'Page.navigate' && new URL(params.url).origin !== 'https://chatgpt.com') throw new Error('Unexpected navigation origin');
  await attach(tabId);
  return await chrome.debugger.sendCommand({tabId}, request.method, params);
}
host.onMessage.addListener(async request => {
  try { host.postMessage({id:request.id,result:await execute(request)}); }
  catch (error) { host.postMessage({id:request.id,error:{message:String(error.message).slice(0,200)}}); }
});
chrome.debugger.onDetach.addListener(source=>attached.delete(source.tabId));
host.onDisconnect.addListener(()=>{});
