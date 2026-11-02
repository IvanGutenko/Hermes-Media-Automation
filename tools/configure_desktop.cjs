// Run with the installed Electron binary. Uses Hermes' own native token store format.
const {app, safeStorage} = require('electron');
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const dataDir = path.join(os.homedir(), '.config', 'Hermes');
app.setPath('userData', dataDir);
app.setName('Hermes');
app.commandLine.appendSwitch('password-store', 'kwallet6');
app.whenReady().then(async () => {
  const login = JSON.parse(fs.readFileSync(path.join(os.homedir(), '.config/hermes-server/login.json'), 'utf8'));
  const response = await fetch(login.url + '/auth/password-login', {
    method: 'POST', headers: {'Content-Type':'application/json'},
    body: JSON.stringify({provider:'basic',username:login.username,password:login.password})
  });
  if (!response.ok) throw new Error('Backend login failed: '+response.status);
  const cookies = response.headers.getSetCookie();
  const cookie = name => {
    const raw = cookies.find(c => c.startsWith(name+'='));
    if (!raw) throw new Error('Missing session cookie');
    return {value:raw.split(';')[0].slice(name.length+1),
            age:Number((raw.match(/max-age=(\d+)/i)||[])[1]||900)};
  };
  const access = cookie('hermes_session_at');
  const refresh = cookie('hermes_session_rt');
  if (!safeStorage.isEncryptionAvailable() || safeStorage.getSelectedStorageBackend() === 'basic_text') {
    throw new Error('OS keyring unavailable; refusing to persist plaintext session tokens');
  }
  console.log('Native credential backend: '+safeStorage.getSelectedStorageBackend());
  const tokens = {accessToken:access.value,refreshToken:refresh.value,
    expiresAt:Math.floor(Date.now()/1000)+access.age,provider:'basic',userId:login.username};
  const envelope = {encoding:'safeStorage',value:safeStorage.encryptString(JSON.stringify(tokens)).toString('base64')};
  fs.mkdirSync(dataDir,{recursive:true,mode:0o700});
  fs.writeFileSync(path.join(dataDir,'secure-token-storage.json'),JSON.stringify({on:true,migrated:true}),{mode:0o600});
  const tokenPath = path.join(dataDir,'native-oauth-tokens.json');
  const stored = fs.existsSync(tokenPath) ? JSON.parse(fs.readFileSync(tokenPath,'utf8')) : {};
  stored[login.url] = envelope;
  fs.writeFileSync(tokenPath,JSON.stringify(stored,null,2),{mode:0o600});
  const configPath = path.join(dataDir,'connection.json');
  const previous = fs.existsSync(configPath) ? JSON.parse(fs.readFileSync(configPath,'utf8')) : {};
  if (previous.remote?.url && previous.remote.url !== login.url) throw new Error('Existing desktop remote requires explicit merge');
  fs.writeFileSync(configPath,JSON.stringify({...previous,mode:'remote',remote:{url:login.url,authMode:'oauth'}},null,2),{mode:0o600});
  const registryPath = path.join(dataDir,'connections.json');
  const registry = fs.existsSync(registryPath) ? JSON.parse(fs.readFileSync(registryPath,'utf8')) : {version:2,connections:[]};
  registry.connections = registry.connections.filter(c => c.id !== 'gut-server-2');
  registry.connections.push({id:'gut-server-2',kind:'remote',label:'gut-server-2',url:login.url,authMode:'oauth'});
  registry.primary = 'gut-server-2'; registry.lastUsed = 'gut-server-2';
  fs.writeFileSync(registryPath,JSON.stringify(registry,null,2),{mode:0o600});
  console.log('Desktop connection saved; native session tokens encrypted with OS keyring');
  app.quit();
}).catch(error => {console.error(error.message);app.exit(1);});
