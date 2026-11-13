// 离线优先：preview 把基础库内置到已缓存的 HTML，避免 iOS 单独读取 JSON 失败导致整个离线查询退回精选。
const CACHE='sv-tactical-iphone-v1.2.1';
const ASSETS=['./','./index.html','./icon.svg','./apple-touch-icon.png','./manifest.webmanifest','./version.json'];
const TERA_NUMBERS=Array.from({length:18},(_,i)=>i+1);
const TERA_ICON_PATH='sprites/types/generation-ix/scarlet-violet/small/Tera/';
const TERA_ICON_URLS=TERA_NUMBERS.map(n=>'https://raw.githubusercontent.com/PokeAPI/sprites/master/'+TERA_ICON_PATH+n+'.png');
const TERA_ICON_FALLBACK_URLS=TERA_NUMBERS.map(n=>'https://cdn.jsdelivr.net/gh/PokeAPI/sprites@master/'+TERA_ICON_PATH+n+'.png');
const SV_TYPE_PATH='sprites/types/generation-ix/scarlet-violet/small/';
const SV_TYPE_URLS=TERA_NUMBERS.map(n=>'https://raw.githubusercontent.com/PokeAPI/sprites/master/'+SV_TYPE_PATH+n+'.png');
const SV_TYPE_FALLBACK_URLS=TERA_NUMBERS.map(n=>'https://cdn.jsdelivr.net/gh/PokeAPI/sprites@master/'+SV_TYPE_PATH+n+'.png');
const isSVTypeIcon=url=>((url.hostname==='raw.githubusercontent.com'&&url.pathname.startsWith('/PokeAPI/sprites/master/'+SV_TYPE_PATH))||(url.hostname==='cdn.jsdelivr.net'&&url.pathname.startsWith('/gh/PokeAPI/sprites@master/'+SV_TYPE_PATH)))&&/\/(?:[1-9]|1[0-8])\.png$/.test(url.pathname);
const isTeraIcon=url=>((url.hostname==='raw.githubusercontent.com'&&url.pathname.startsWith('/PokeAPI/sprites/master/'+TERA_ICON_PATH))||(url.hostname==='cdn.jsdelivr.net'&&url.pathname.startsWith('/gh/PokeAPI/sprites@master/'+TERA_ICON_PATH)))&&/\/(?:[1-9]|1[0-8])\.png$/.test(url.pathname);

self.addEventListener('install',event=>{
  event.waitUntil(caches.open(CACHE).then(cache=>cache.addAll(ASSETS)).then(()=>self.skipWaiting()));
});
self.addEventListener('activate',event=>{
  event.waitUntil(caches.keys().then(keys=>Promise.all(keys.filter(k=>k.startsWith('sv-tactical-')&&k!==CACHE).map(k=>caches.delete(k)))).then(()=>self.clients.claim()));
});
self.addEventListener('fetch',event=>{
  if(event.request.method!=='GET')return;
  const url=new URL(event.request.url);
  const scope=new URL(self.registration.scope);
  if(isTeraIcon(url)||isSVTypeIcon(url)){
    event.respondWith(caches.match(event.request).then(hit=>hit||fetch(event.request).then(response=>{
      // The third-party sprite server may return an opaque no-cors response.
      if(response.ok||response.type==='opaque')caches.open(CACHE).then(cache=>cache.put(event.request,response.clone())).catch(()=>{});
      return response;
    })));
    return;
  }
  if(url.origin!==scope.origin||!url.pathname.startsWith(scope.pathname))return;
  const navigation=event.request.mode==='navigate';
  const checkVersion=url.pathname.endsWith('/version.json');
  if(navigation||checkVersion){
    // 在线时尽量获取最新版本；断网时使用已缓存页面。
    event.respondWith(fetch(event.request).then(response=>{
      if(response.ok){const copy=response.clone();caches.open(CACHE).then(cache=>cache.put(navigation?'./index.html':'./version.json',copy)).catch(()=>{});}
      return response;
    }).catch(async()=> (await caches.match(event.request,{ignoreSearch:true}))||(navigation?(await caches.match('./index.html')):null)||Response.error()));
    return;
  }
  // 仅缓存应用清单内的同源文件，防止无意义的缓存膨胀。
  const matched=ASSETS.some(entry=>new URL(entry,scope).pathname===url.pathname);
  if(matched)event.respondWith(caches.match(event.request,{ignoreSearch:true}).then(hit=>hit||fetch(event.request).then(response=>{
    if(response.ok){const copy=response.clone();caches.open(CACHE).then(cache=>cache.put(event.request,copy)).catch(()=>{});}
    return response;
  })));
});

// Called on first online launch so all 18 original symbols are ready for offline reference.
// This is noncritical: a temporary asset CDN failure must not prevent the app from launching.
self.addEventListener('message',event=>{
  if(!['CACHE_TERA_ICONS','CACHE_SV_TYPE_ICONS'].includes(event.data?.type))return;
  const cacheTera=event.data.type==='CACHE_TERA_ICONS';
  const primary=cacheTera?TERA_ICON_URLS:SV_TYPE_URLS;
  const alternative=cacheTera?TERA_ICON_FALLBACK_URLS:SV_TYPE_FALLBACK_URLS;
  event.waitUntil((async()=>{
    const cache=await caches.open(CACHE);
    await Promise.allSettled(TERA_NUMBERS.map(async(n,i)=>{
      for(const u of [primary[i],alternative[i]]){
        if(await cache.match(u))return;
        try{
          const response=await fetch(u,{mode:'no-cors'});
          if(response.ok||response.type==='opaque'){
            await cache.put(u,response);
            return;
          }
        }catch{}
      }
    }));
    const cached=(await Promise.all(TERA_NUMBERS.map(async(n,i)=>Boolean((await cache.match(primary[i]))||(await cache.match(alternative[i])))))).filter(Boolean).length;
    event.source?.postMessage?.({type:cacheTera?'TERA_CACHE_COUNT':'SV_TYPE_CACHE_COUNT',count:cached,total:18});
  })());
});
