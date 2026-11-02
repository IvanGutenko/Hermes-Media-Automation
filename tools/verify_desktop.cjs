// Check the same encrypted native credentials and JSON-RPC transport used by Desktop.
const {app,safeStorage}=require('electron');
const fs=require('node:fs'), path=require('node:path'), os=require('node:os');
const root=path.join(os.homedir(),'.config/Hermes');
app.setName('Hermes');app.setPath('userData',root);
app.commandLine.appendSwitch('password-store','kwallet6');
let socket;
const deadline=setTimeout(()=>{console.error('Desktop transport check timed out');app.exit(1)},90000);
app.whenReady().then(async()=>{
 const url='http://100.83.195.10:9119';
 const envelope=JSON.parse(fs.readFileSync(path.join(root,'native-oauth-tokens.json'),'utf8'))[url];
 const tokens=JSON.parse(safeStorage.decryptString(Buffer.from(envelope.value,'base64')));
 const headers={Authorization:'Bearer '+tokens.accessToken,'Content-Type':'application/json'};
 const identity=await fetch(url+'/api/auth/me',{headers});
 if(!identity.ok)throw new Error('Native authorization failed: '+identity.status);
 const ticketResponse=await fetch(url+'/api/auth/ws-ticket',{method:'POST',headers,body:'{}'});
 if(!ticketResponse.ok)throw new Error('WS ticket failed: '+ticketResponse.status);
 const ticket=await ticketResponse.json();
 socket=new WebSocket(url.replace('http:','ws:')+'/api/ws?ticket='+encodeURIComponent(ticket.ticket));
 let serial=0;const pending=new Map();const events=new Set();let sessionId;
 const rpc=(method,params={})=>new Promise((resolve,reject)=>{
  const id=++serial;pending.set(id,{resolve,reject});
  socket.send(JSON.stringify({jsonrpc:'2.0',id,method,params})+'\n');
 });
 socket.onmessage=event=>{
  for(const line of String(event.data).trim().split('\n')){
   const frame=JSON.parse(line);
   if(frame.id && pending.has(frame.id)){
    const promise=pending.get(frame.id);pending.delete(frame.id);
    frame.error?promise.reject(new Error(JSON.stringify(frame.error))):promise.resolve(frame.result);
   }else if(frame.method){
    const type=frame.method==='event'?frame.params?.type:frame.method;
    events.add(type);
    if(type==='message.complete'){
     const text=frame.params?.payload?.text||'';
     if(/воскрес/iu.test(text) && /нет|не заплан|не провод|отдых/iu.test(text)){
      console.log('Verified desktop response:',text);
      console.log('Desktop RPC turn finished',sessionId,'events:',[...events]);
      clearTimeout(deadline);socket.close();app.quit();
     }
    }
   }
  }
 };
 await new Promise((resolve,reject)=>{socket.onopen=resolve;socket.onerror=()=>reject(new Error('WebSocket open failed'))});
 console.log('Native authorization and desktop WebSocket connected');
 const created=await rpc('session.create',{cwd:'/home/gut/hermes-workspace',title:'Проверка подключения с ПК',source:'desktop'});
 sessionId=created.session_id;
 console.log('Created session:',sessionId);
 const sent=await rpc('prompt.submit',{session_id:sessionId,text:'Прочитай Health/Комплекс физ-упражнений.md из выбранных заметок Obsidian. На 25 октября 2026 запланирована силовая тренировка? Ответь кратко, учитывая день недели.'});
 console.log('Desktop prompt accepted:',JSON.stringify(sent).slice(0,300));
 // Emit compact event names if the server uses another completion vocabulary.
 setTimeout(()=>console.log('Desktop events so far:',[...events]),20000);
}).catch(error=>{console.error(error.message);clearTimeout(deadline);app.exit(1)});
