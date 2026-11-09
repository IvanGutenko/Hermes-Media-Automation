"""Object-led, frame-addressable motion scenes; all editorial copy stays source-bound."""
import html
import json
import math

SYSTEM = 'editorial-v2'
LAYOUTS = {'kinetic', 'compare', 'workflow', 'terminal', 'spotlight', 'editor', 'network', 'browser'}
COLORS = {
    'cobalt': {'paper':'#edf3fc', 'ink':'#172548', 'blue':'#315cfa', 'light':'#a9caff', 'warm':'#ffb790'},
    'plum': {'paper':'#fbf0f4', 'ink':'#36233f', 'blue':'#884bba', 'light':'#dfb7f5', 'warm':'#ffbb99'},
    'slate': {'paper':'#eff7f6', 'ink':'#173b47', 'blue':'#187986', 'light':'#9ddcdb', 'warm':'#ffc891'},
}


def speech_cues(scene, duration):
    """Use the verified recording's words when present; identify estimates explicitly."""
    supplied = scene.get('_speech_words')
    if supplied:
        cues=[]
        for word in supplied:
            start,end=float(word['start']),float(word['end'])
            if not (math.isfinite(start) and math.isfinite(end) and 0 <= start <= end <= duration+.06):
                raise ValueError('Motion caption has invalid recorded word timing')
            cues.append({'word':str(word['word']), 'start':start, 'end':min(duration,end)})
        if any(a['start'] > b['start'] for a,b in zip(cues,cues[1:])):
            raise ValueError('Motion caption word times are out of order')
        return cues,'recorded_words'
    words=scene['voiceover_ru'].split()
    weights=[max(2,len(word)) for word in words]
    total=sum(weights);cursor=.08
    cues=[]
    for word,weight in zip(words,weights):
        end=cursor+(duration-.18)*weight/total
        cues.append({'word':word,'start':cursor,'end':end})
        cursor=end
    return cues,'estimated_text_weights'


def action_times(scene, duration):
    """One shared cue sheet drives the visual actions and quiet, original foley."""
    count=len(scene['motion']['items'])
    return [round(.18+i*min(.72,(duration*.56-.18)/max(1,count-1)),4) for i in range(count)]


def _json(value):
    # Source strings never get to close an inline script, including in captions.
    return json.dumps(value,ensure_ascii=False).replace('<','\\u003c').replace('>','\\u003e').replace('&','\\u0026')


def composition(scene, duration, palette):
    spec=scene['motion'];layout=spec['layout'];hybrid=scene['visual_type']=='hybrid'
    colors=COLORS[palette]
    esc=lambda text:html.escape(text,quote=True)
    cues,timing=speech_cues(scene,duration)
    items=spec['items']
    def label(value,cls=''):
        return f'<span class="copy fit {cls}" data-copy>{esc(value)}</span>'
    if layout=='editor':
        tracks=''.join(f'<div class="track actor"><div class="track-name">{label(value)}</div><div class="track-clips"><i></i><i></i><i></i></div></div>' for value in items)
        hero=f'''<div class="editor-object object"><div class="window-top"><i></i><i></i><i></i></div>
        <div class="monitor"><div class="monitor-type">Aa</div><div class="monitor-film"><i></i><i></i></div><svg class="wave" viewBox="0 0 240 60"><path d="M0 30H20L27 12L35 48L43 20L50 38L58 8L66 52L74 22L82 36L90 16L98 44L106 4L114 56L122 17L130 43L138 10L146 50L154 20L162 40L170 15L178 45L186 24L194 36L202 20L210 40L218 27H240"/></svg></div>
        <div class="tracks">{tracks}<div class="playhead"><i></i></div></div></div>'''
    elif layout in ('workflow','network'):
        nodes=''.join(f'<div class="node actor node-{i}"><i class="socket"></i>{label(value)}</div>' for i,value in enumerate(items))
        if layout=='workflow':
            lines=''.join(f'<path class="connector" d="M40 {69+i*115} V{69+(i+1)*115}"/>' for i in range(len(items)-1))
        else:
            points=[(140,115),(420,115),(140,350),(420,350)]
            lines=''.join(f'<path class="connector" d="M{points[i][0]} {points[i][1]} L{points[i+1][0]} {points[i+1][1]}"/>' for i in range(len(items)-1))
        hero=f'<div class="graph-object object"><svg class="connections" viewBox="0 0 560 500">{lines}</svg>{nodes}<div class="traveller"></div></div>'
    elif layout in ('terminal','browser'):
        rows=''.join(f'<div class="code-row actor"><i></i>{label(value)}</div>' for value in items)
        hero=f'<div class="browser-object object"><div class="window-top"><i></i><i></i><i></i><b></b></div><div class="browser-lines">{rows}</div><div class="selection"></div><div class="cursor"><svg viewBox="0 0 48 58"><path d="M4 2L42 31L25 33L18 51Z"/></svg></div></div>'
    elif layout=='compare':
        hero='<div class="compare-object object">'+''.join(f'<div class="compare-side actor"><div class="comparison-sculpture"><i></i><i></i><i></i></div>{label(value)}</div>' for value in items)+'</div>'
    elif layout=='kinetic':
        hero='<div class="type-object object"><div class="output-outline"></div>'+''.join(f'<div class="type-slab actor slab-{i}">{label(value)}</div>' for i,value in enumerate(items))+'</div>'
    else:
        hero='<div class="focus-object object"><div class="orbit orbit-back"></div><div class="orbit orbit-front"></div><div class="focus-core"><i></i><i></i><i></i></div>'+''.join(f'<div class="focus-tag actor tag-{i}">{label(value)}</div>' for i,value in enumerate(items))+'</div>'
    variables=';'.join(f'--{key}:{value}' for key,value in colors.items())
    return '''<!doctype html><html lang="ru"><meta charset="utf-8"><style>
@font-face{font-family:Display;src:url("MotionDisplay.ttf");font-weight:200 800}
*{box-sizing:border-box}html,body{margin:0;width:720px;height:1280px;overflow:hidden;background:transparent}
body{font-family:Display,sans-serif;font-weight:650;color:var(--ink)}
#stage{position:absolute;inset:0;overflow:hidden;background:var(--paper)}
.light{position:absolute;inset:-100px;background:radial-gradient(ellipse at 12% 27%,white 0,transparent 54%),radial-gradient(ellipse at 92% 69%,var(--light) 0,transparent 57%);opacity:.8}
.ground{position:absolute;left:40px;top:890px;width:590px;height:100px;border-radius:50%;background:var(--blue);opacity:.11;filter:blur(32px)}
.world{position:absolute;inset:0;perspective:1500px;transform-origin:50% 53%}
.headline{position:absolute;left:58px;top:125px;width:564px;height:205px;font-size:64px;font-weight:800;line-height:1.06;letter-spacing:-2.5px;white-space:pre-line;overflow-wrap:anywhere}
.hero{position:absolute;left:58px;top:385px;width:560px;height:500px;perspective:1100px;transform-style:preserve-3d}
.copy{display:block;overflow-wrap:anywhere;line-height:1.3}
.object{position:absolute;inset:0;transform-style:preserve-3d;color:var(--ink)}
.kicker{position:absolute;left:58px;top:918px;width:558px;height:78px;font-size:27px;line-height:1.25;letter-spacing:-.6px;color:var(--ink)}
.kicker.brand{font-size:41px;font-weight:800;color:var(--blue);height:98px;top:891px}
.captions{position:absolute;left:58px;top:1036px;width:558px;height:94px;display:flex;flex-wrap:wrap;align-content:center;justify-content:center;gap:3px 7px;font-size:35px;font-weight:800;line-height:1.32;letter-spacing:-1px;color:var(--ink)}
.caption-word{display:inline-block;padding:1px 5px;border-radius:8px;white-space:nowrap}
.caption-word.active{color:white;background:var(--blue);box-shadow:0 5px 14px #315cfa20}
.hybrid#stage{background:transparent;color:white}.hybrid .light{background:linear-gradient(180deg,#07101ccc 0,#07101c30 32%,transparent 48%,#07101cbb 72%,#07101cf5 100%);inset:-100px;opacity:1}
.hybrid .ground{display:none}.hybrid .headline{text-shadow:0 3px 24px #0008}
.hybrid .kicker,.hybrid .captions{color:white}
.hybrid .focus-core{display:none}.hybrid .orbit{border-color:#edf3fc55}
.hybrid .focus-tag{background:#f6f9ffee;color:#172548;border-color:#ffffff60;box-shadow:0 18px 40px #0004}
.kinetic#stage{background:var(--ink)}.kinetic .light{background:radial-gradient(ellipse at 85% 48%,var(--blue),transparent 65%);opacity:.45}
.kinetic .headline,.kinetic .captions,.kinetic .kicker{color:var(--paper)}.kinetic .kicker.brand{color:var(--light)}
.kinetic .type-slab{color:var(--paper)}.kinetic .slab-1{color:var(--warm)}
.hybrid.kinetic#stage{background:transparent}.hybrid.kinetic .light{background:linear-gradient(180deg,#07101ccc,transparent 45%,#07101cf5);opacity:1}
.orbit{position:absolute;border:2px solid var(--blue);border-radius:50%;left:54px;top:0;width:452px;height:350px;transform:rotate(-28deg);opacity:.45}
.orbit-front{left:98px;top:24px;width:360px;height:305px;transform:rotate(26deg);border-width:1px}
.orbit:after{content:"";position:absolute;width:14px;height:14px;left:60px;top:32px;border-radius:50%;background:var(--warm);box-shadow:0 0 22px var(--warm)}
.focus-core{position:absolute;left:154px;top:40px;width:250px;height:250px;border-radius:50%;background:radial-gradient(circle at 35% 28%,white,var(--light) 23%,var(--blue) 64%,var(--ink));box-shadow:inset 5px 9px 20px #fff9,0 35px 50px #17254830}
.focus-core i{position:absolute;inset:39px;border:1px solid #fff7;border-radius:50%}.focus-core i:nth-child(2){inset:69px}.focus-core i:nth-child(3){inset:96px;background:var(--paper)}
.focus-tag{position:absolute;left:8px;top:305px;max-width:480px;min-height:72px;padding:17px 27px;border-radius:20px;border:1px solid #fff9;background:#ffffffde;box-shadow:0 18px 35px #17254818;font-size:35px;transform:rotate(-3deg)}
.focus-tag:nth-of-type(even){left:auto;right:0;background:var(--blue);color:white;transform:rotate(3deg)}
.tag-1{top:400px}.tag-2{top:270px}.tag-3{top:175px}
.editor-object{top:8px;height:480px;border:1px solid #ffffffdd;border-radius:24px;background:linear-gradient(135deg,#fffffffa,#dae7fbfa);box-shadow:0 30px 60px #17254830,0 4px 6px #17254815;overflow:hidden}
.window-top{height:45px;border-bottom:1px solid #17254812;display:flex;align-items:center;gap:7px;padding:0 18px;background:#fff8}
.window-top>i{height:9px;width:9px;border-radius:50%;background:var(--ink);opacity:.2}.window-top b{height:13px;width:150px;background:var(--paper);border-radius:5px;margin-left:38px}
.monitor{position:relative;height:135px;margin:16px 18px;background:var(--ink);border-radius:14px;overflow:hidden;display:flex;align-items:center;justify-content:center}
.monitor-type{font-size:86px;color:white;font-weight:800;letter-spacing:-7px;position:absolute;left:44px;top:10px;z-index:2}
.monitor-film{position:absolute;left:265px;top:24px;width:150px;height:103px;border-radius:13px;transform:rotate(-9deg);background:var(--warm);box-shadow:0 8px 22px #0004}
.monitor-film i{position:absolute;border:3px solid var(--ink);left:37px;top:16px;width:74px;height:74px;border-radius:50%;opacity:.7}.monitor-film i+i{inset:36px 57px;width:34px;height:34px;background:var(--ink)}
.wave{position:absolute;left:220px;top:64px;width:270px;height:60px;stroke:var(--light);fill:none;stroke-width:3}
.tracks{position:relative;padding:1px 18px 16px}.track{display:flex;align-items:center;gap:16px;height:60px;border-bottom:1px solid #1725480c}
.track-name{width:230px;font-size:26px;font-weight:750;flex-shrink:0}.track-clips{flex:1;display:flex;gap:5px;align-items:center;height:36px}
.track-name .copy{max-height:48px}
.track-clips>i{height:32px;width:44%;border-radius:6px;background:var(--blue);box-shadow:inset 0 1px #fff5}.track-clips>i+i{width:32%;opacity:.8}.track-clips>i+i+i{width:24%;opacity:.55}
.track:nth-child(2) .track-clips>i{background:#7097da}.track:nth-child(3) .track-clips>i{background:#c98168}.track:nth-child(4) .track-clips>i{background:#719d98}
.playhead{position:absolute;left:272px;top:0;height:100%;width:2px;background:var(--ink)}.playhead i{position:absolute;left:-5px;top:0;width:12px;height:12px;background:var(--ink);clip-path:polygon(0 0,100% 0,100% 65%,50% 100%,0 65%)}
.graph-object{padding:0}.connections{position:absolute;inset:0;width:560px;height:500px;stroke:var(--blue);stroke-width:4;fill:none}.connector{stroke-dasharray:800;stroke-dashoffset:800}
.node{position:absolute;background:#fff;box-shadow:0 18px 34px #17254817,inset 0 1px #fff;border:1px solid #fff;border-radius:22px;display:flex;align-items:center;min-height:94px;padding:19px 22px 19px 48px;font-size:30px;width:450px;left:70px}
.workflow .node-0{top:22px}.workflow .node-1{top:137px}.workflow .node-2{top:252px}.workflow .node-3{top:367px}.workflow .socket{left:-38px;background:var(--blue);border:5px solid var(--paper)}
.socket{position:absolute;left:17px;top:calc(50% - 9px);width:18px;height:18px;border:5px solid var(--light);background:var(--blue);border-radius:50%}
.network .node{width:252px;min-height:176px;font-size:29px;padding:30px 20px 20px;align-items:center;text-align:center;justify-content:center}.network .socket{left:calc(50% - 9px);top:13px}
.network .node-0{left:0;top:27px}.network .node-1{left:283px;top:27px}.network .node-2{left:0;top:270px}.network .node-3{left:283px;top:270px}
.traveller{position:absolute;width:14px;height:14px;border-radius:50%;left:33px;top:52px;background:var(--warm);box-shadow:0 0 0 7px #ffb79040}
.browser-object{top:25px;height:430px;background:#fff;border-radius:22px;box-shadow:0 30px 60px #17254826;overflow:hidden;border:1px solid #fff}
.browser-lines{padding:25px 28px}.code-row{position:relative;min-height:65px;padding:13px 12px 13px 28px;border-bottom:1px solid #17254812;font-size:27px}.code-row i{position:absolute;left:1px;top:25px;width:8px;height:8px;background:var(--blue);border-radius:2px}
.terminal .browser-object{background:var(--ink);color:var(--paper)}.terminal .window-top{background:#fff1}.terminal .code-row{font-family:monospace;font-size:25px;border-color:#fff1}.terminal .window-top>i{background:white}
.selection{position:absolute;left:25px;top:71px;width:503px;height:65px;border:2px solid var(--blue);border-radius:9px;pointer-events:none;background:#315cfa08}
.cursor{position:absolute;left:432px;top:300px;width:36px;height:45px;filter:drop-shadow(0 3px 3px #17254840)}.cursor svg{fill:var(--ink);stroke:var(--paper);stroke-width:3}
.compare-object{display:flex;gap:22px;align-items:center}.compare-side{position:relative;width:266px;min-height:345px;padding:24px;border-radius:24px;background:#fff;box-shadow:0 25px 40px #1725481c;transform:rotate(-5deg);font-size:31px}
.compare-side+.compare-side{background:var(--blue);color:white;transform:rotate(5deg);top:50px}
.comparison-sculpture{position:relative;height:170px;margin-bottom:24px}.comparison-sculpture i{position:absolute;left:25px;top:50px;width:155px;height:52px;border:2px solid var(--blue);border-radius:15px;transform:rotate(-12deg)}.comparison-sculpture i+i{top:33px;left:40px}.comparison-sculpture i+i+i{top:16px;left:55px;background:var(--paper)}
.compare-side+.compare-side .comparison-sculpture i{border-color:#fff9;background:#ffffff20}.compare-side+.compare-side .comparison-sculpture i+i+i{background:var(--light)}
.type-object{padding:0}.type-slab{position:absolute;left:0;top:10px;font-size:82px;font-weight:800;line-height:1;letter-spacing:-3px;max-width:500px;color:var(--blue);text-shadow:1px 1px #a9caff,2px 3px #a9caff,3px 5px #a9caff,4px 7px #a9caff,5px 9px #a9caff,7px 14px 20px #17254817;transform:rotate(-5deg)}
.type-slab .copy{max-height:145px;padding:0 6px 22px 0}.slab-1 .copy{max-width:460px}
.slab-1{left:65px;top:150px;color:var(--ink);transform:rotate(4deg);font-size:72px}.slab-2{left:20px;top:290px;font-size:81px;transform:rotate(-3deg)}.slab-3{left:160px;top:405px;font-size:56px}
.output-outline{position:absolute;left:130px;top:-20px;width:330px;height:470px;border:2px solid #a9caff80;border-radius:30px;background:linear-gradient(150deg,#a9caff20,#315cfa10);box-shadow:0 28px 55px #17254815;opacity:0}
</style><div id="stage" class="''' + ('hybrid ' if hybrid else '')+layout+'''" style="'''+variables+'''">
<div class="light"></div><div class="ground"></div><div class="world">
<div class="headline fit" data-copy>'''+esc(spec['headline'])+'''</div><div class="hero">'''+hero+'''</div>
<div class="kicker fit '''+('brand' if '@' in spec.get('kicker','') else '')+'''" data-copy>'''+esc(spec.get('kicker',''))+'''</div></div>
<div class="captions" aria-label="Русская речь"></div></div><script src="gsap.min.js"></script><script>
const duration='''+str(duration)+''', layout='''+_json(layout)+''', cues='''+_json(cues)+''', actionTimes='''+_json(action_times(scene,duration))+''';
const tl=gsap.timeline({paused:true}), actors=Array.from(document.querySelectorAll('.actor'));
tl.fromTo('.headline',{y:25,rotation:-1},{y:0,rotation:0,duration:.55,ease:'power3.out'},0);
tl.fromTo('.object',{rotationY:-9,rotationX:5,z:-70},{rotationY:3,rotationX:-2,z:0,duration:duration*.75,ease:'power1.inOut'},0);
tl.to('.light',{x:-35,y:25,duration:duration,ease:'none'},0);
actors.forEach((e,i)=>{tl.fromTo(e,{y:38,opacity:0,scale:.91},{y:0,opacity:1,scale:1,duration:.48,ease:'back.out(1.12)'},actionTimes[i]);});
if(layout==='editor'){
 tl.fromTo('.monitor-type',{x:-20,rotation:-12},{x:0,rotation:2,duration:.7,ease:'power3.out'},.08);
 tl.fromTo('.monitor-film',{x:80,rotation:14,scale:.75},{x:0,rotation:-9,scale:1,duration:.65,ease:'back.out(1.2)'},.55);
 tl.fromTo('.wave',{opacity:0,scaleX:.2},{opacity:1,scaleX:1,duration:.6},1);
 tl.to('.playhead',{x:220,duration:Math.max(.1,duration-1.2),ease:'none'},.7);
 tl.to('.monitor-type',{x:25,scale:.83,duration:1,ease:'power2.inOut'},duration*.51);
 tl.to('.monitor-film',{x:-15,rotation:3,duration:1,ease:'power2.inOut'},duration*.51);
 tl.to('.editor-object',{rotationZ:-3,y:-12,scale:1.035,duration:duration*.35,ease:'power2.inOut'},duration*.6);
}else if(layout==='workflow'||layout==='network'){
 document.querySelectorAll('.connector').forEach((e,i)=>tl.to(e,{strokeDashoffset:0,duration:.6,ease:'power2.inOut'},actionTimes[i+1]||.9));
 tl.to('.traveller',{y:layout==='workflow'?Math.max(0,(actors.length-1)*115):300,x:layout==='workflow'?0:400,duration:duration*.6,ease:'power2.inOut'},.5);
 actors.forEach((e,i)=>tl.to(e,{scale:1.035,duration:.22,yoyo:true,repeat:1,ease:'power2.inOut'},Math.min(duration-.8,actionTimes[i]+.5)));
}else if(layout==='browser'||layout==='terminal'){
 actors.forEach((e,i)=>tl.to('.selection',{y:i*65,duration:.36,ease:'power3.inOut'},actionTimes[i]+.2));
 tl.to('.cursor',{x:-130,y:-195,duration:.8,ease:'power3.inOut'},.25);
 tl.to('.cursor',{scale:.78,duration:.12,yoyo:true,repeat:1},1.05);
 tl.to('.browser-object',{rotationZ:-2,scale:1.04,duration:1.1},duration*.58);
}else if(layout==='spotlight'){
 tl.to('.orbit-back',{rotation:'+=34',duration:duration,ease:'none'},0);
 tl.to('.orbit-front',{rotation:'-=47',duration:duration,ease:'none'},0);
 tl.to('.focus-core',{rotation:22,scale:1.045,duration:duration,ease:'power1.inOut'},0);
 actors.forEach((e,i)=>tl.to(e,{x:i%2?-12:14,rotation:i%2?-2:2,duration:Math.max(.1,duration-actionTimes[i]-.5),ease:'sine.inOut'},actionTimes[i]+.48));
}else if(layout==='compare'){
 tl.to('.compare-side:first-child',{y:-30,rotation:-2,duration:.7},duration*.28);
 tl.to('.compare-side:last-child',{y:-30,rotation:0,scale:1.04,duration:.7},duration*.52);
}else{
 actors.forEach((e,i)=>tl.to(e,{x:i%2?-12:18,rotation:i%2?-2:1,duration:duration*.38,ease:'power2.inOut'},duration*.27));
 tl.to('.output-outline',{opacity:1,duration:.45},duration*.62);
 actors.forEach((e,i)=>tl.to(e,{
  scale:()=>Math.min(.68,290/e.offsetWidth),
  x:()=>295-e.offsetLeft-e.offsetWidth/2,
  y:()=>65+i*135-e.offsetTop-e.offsetHeight/2,
  rotation:0,duration:.75,ease:'power3.inOut'},duration*.62));
}
tl.fromTo('.kicker',{y:18,opacity:0},{y:0,opacity:1,duration:.45,ease:'power3.out'},Math.min(duration*.55,2.1));
tl.to('.world',{scale:1.035,duration:.2,ease:'power2.in'},Math.max(0,duration-.2));
let lastGroup=-1;
function captions(t){
 let index=cues.findIndex(w=>t>=w.start&&t<=w.end);
 if(index<0){index=cues.findIndex(w=>w.start>t)-1;if(index<0)index=t>=(cues.at(-1)?.end||0)?cues.length-1:0;}
 const group=Math.floor(index/3),container=document.querySelector('.captions');
 if(group!==lastGroup){container.replaceChildren();cues.slice(group*3,group*3+3).forEach(w=>{const span=document.createElement('span');span.className='caption-word';span.textContent=w.word;container.appendChild(span);});lastGroup=group;}
 Array.from(container.children).forEach((e,i)=>e.classList.toggle('active',group*3+i===index));
}
window.seek=t=>{t=Math.max(0,Math.min(t,duration));tl.seek(t,true);captions(t);return true;};
window.__timelines={scene:tl};window.__captionTiming='''+_json(timing)+''';window.__audioCues=actionTimes;
window.fitText=()=>{
 document.querySelectorAll('.fit').forEach(e=>{
  for(let i=0;i<35&&(e.scrollWidth>e.clientWidth+1||e.scrollHeight>e.clientHeight+1);i++)e.style.fontSize=(parseFloat(getComputedStyle(e).fontSize)*.95)+'px';
 });
 // Fit the longest live phrase before encoding; changing phrases never changes the font size.
 const box=document.querySelector('.captions');let max=35;
 for(let i=0;i<cues.length;i+=3){box.textContent=cues.slice(i,i+3).map(w=>w.word).join(' ');box.style.fontSize=max+'px';while((box.scrollHeight>box.clientHeight+1||box.scrollWidth>box.clientWidth+1)&&max>23){max--;box.style.fontSize=max+'px';}}
 box.textContent='';lastGroup=-1;captions(0);
};
window.layoutProblems=()=>{
 const errors=[];
 document.querySelectorAll('[data-copy]').forEach(e=>{const r=e.getBoundingClientRect();if(e.scrollWidth>e.clientWidth+2||r.left<32||r.right>652||r.top<65||r.bottom>1015)errors.push('copy out of safe frame: '+e.textContent);});
 const head=document.querySelector('.headline').getBoundingClientRect(),hero=document.querySelector('.hero').getBoundingClientRect();
 if(head.bottom>hero.top-12)errors.push('headline overlaps hero');
 return errors;
};
window.seek(0);
</script></html>'''
