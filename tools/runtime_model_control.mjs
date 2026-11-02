// Use Hermes' authenticated Desktop RPCs to change runtime pins without editing its session DB.
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

const login=JSON.parse(fs.readFileSync(path.join(os.homedir(),'.config/hermes-server/login.json'),'utf8'));
const response=await fetch(login.url+'/auth/password-login',{
 method:'POST',headers:{'Content-Type':'application/json'},
 body:JSON.stringify({provider:'basic',username:login.username,password:login.password})});
if(!response.ok)throw new Error('Backend login failed: '+response.status);
const cookie=response.headers.getSetCookie().find(x=>x.startsWith('hermes_session_at='));
if(!cookie)throw new Error('Missing native session token');
const token=cookie.split(';')[0].slice('hermes_session_at='.length);
const headers={Authorization:'Bearer '+token,'Content-Type':'application/json'};
const ticketResponse=await fetch(login.url+'/api/auth/ws-ticket',{method:'POST',headers,body:'{}'});
if(!ticketResponse.ok)throw new Error('Ticket failed: '+ticketResponse.status);
const ticket=await ticketResponse.json();
const ws=new WebSocket(login.url.replace('http:','ws:')+'/api/ws?ticket='+encodeURIComponent(ticket.ticket));
let counter=0;const pending=new Map();const events=[];
const deadline=setTimeout(()=>{console.error('RPC deadline exceeded');process.exit(1)},360000);
const rpc=(method,params={})=>new Promise((resolve,reject)=>{
 const id=++counter;pending.set(id,{resolve,reject});ws.send(JSON.stringify({jsonrpc:'2.0',id,method,params})+'\n');
});
ws.onmessage=event=>{
 for(const line of String(event.data).trim().split('\n')){
  const frame=JSON.parse(line);
  if(pending.has(frame.id)){
   const p=pending.get(frame.id);pending.delete(frame.id);
   frame.error?p.reject(new Error(JSON.stringify(frame.error))):p.resolve(frame.result);
  }else if(frame.method==='event')events.push(frame.params);
 }
};
await new Promise((resolve,reject)=>{ws.onopen=resolve;ws.onerror=()=>reject(new Error('WebSocket failed'))});
const action=process.argv[2]||'status';
const provider=process.env.HERMES_TARGET_PROVIDER||'gemini';
const model=process.env.HERMES_TARGET_MODEL||'gemini-3.8-flash';
try{
 if(action==='status'){
  const r=await rpc('session.active_list');
  console.log(JSON.stringify(r,null,2));
 }else if(action==='switch'){
  for(const storedId of process.argv.slice(3)){
   // A model pick on a lazy session is only an in-memory override. Hydrate first
   // so the native model switch also persists the selected runtime in state.db.
   const resumed=await rpc('session.resume',{session_id:storedId,omit_messages:true,eager_build:true});
   if(resumed.running)throw new Error('Refusing to interrupt running session '+storedId);
   if(resumed.info?.lazy)throw new Error('Session is still building; model persistence is not confirmed');
   const changed=await rpc('config.set',{key:'model',value:model+' --provider '+provider,session_id:resumed.session_id});
   if(changed.confirm_required)throw new Error('Model needs a separate cost decision');
   const reasoning=await rpc('config.set',{key:'reasoning',value:'ultra',scope:'session',session_id:resumed.session_id});
   console.log(JSON.stringify({stored_id:storedId,model:changed,reasoning}));
  }
 }else if(action==='probe'){
  const created=await rpc('session.create',{cwd:'/home/gut/hermes-workspace',source:'desktop',
   ...(process.env.HERMES_TARGET_PROVIDER||process.env.HERMES_TARGET_MODEL?{model,provider}:{}),
   title:'Проверка модели и reasoning после исправления'});
  console.log('Created',created.stored_session_id,'live',created.session_id);
  console.log('Session info',JSON.stringify(created.info));
  if(process.env.HERMES_TARGET_PROVIDER||process.env.HERMES_TARGET_MODEL){
   if(created.info?.model!==model)throw new Error('Probe session did not accept the pinned model');
   await rpc('config.set',{key:'reasoning',value:'ultra',scope:'session',session_id:created.session_id});
  }
  await rpc('prompt.submit',{session_id:created.session_id,
   text:process.env.HERMES_PROBE_TEXT||'Прочитай инструментом файл knowledge/obsidian/every day tasks.md и назови первые две задачи из него. Кратко. Ничего не сохраняй.'});
  while(true){
   const completed=events.find(e=>e.session_id===created.session_id&&e.type==='message.complete');
   if(completed){
    const text=completed.payload?.text||'';
    if(completed.payload?.status==='error'||!text||/^Error:/i.test(text))throw new Error(text||'Empty/error answer');
    console.log('Received response',text);
    console.log('Final session',JSON.stringify(await rpc('session.status',{session_id:created.session_id})));
    console.log('Final reasoning',JSON.stringify(await rpc('config.get',{key:'reasoning',session_id:created.session_id})));
    break;
   }
   await new Promise(resolve=>setTimeout(resolve,200));
  }
 }else if(action==='inspect'){
  for(const storedId of process.argv.slice(3)){
   const resumed=await rpc('session.resume',{session_id:storedId,omit_messages:true});
   console.log(JSON.stringify({stored_id:storedId,info:resumed.info,
    reasoning:await rpc('config.get',{key:'reasoning',session_id:resumed.session_id})}));
  }
 }else throw new Error('Unknown action');
}finally{clearTimeout(deadline);ws.close();}
