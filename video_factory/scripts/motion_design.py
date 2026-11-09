"""Structured, source-bound motion direction and editable HTML/GSAP compositions.

No model-written JavaScript is executed. The director chooses layouts and exact
copy; the renderer owns the deterministic clock and the local assets.
"""
import hashlib
import html
import json
import math
from pathlib import Path

VERSION = 'html-gsap-v1'
LAYOUTS = {'kinetic', 'compare', 'workflow', 'terminal', 'spotlight'}
from editorial_motion import SYSTEM as EDITORIAL_SYSTEM, LAYOUTS as EDITORIAL_LAYOUTS
PALETTES = {
    'cobalt': ('#1235c5', '#f5f7ff', '#b5caff', '#ffce57'),
    'plum': ('#382451', '#fff8fb', '#dcb8ec', '#f6b898'),
    'slate': ('#182f3d', '#f2fbff', '#a3cadc', '#ffbb8a'),
}

DIRECTOR_PROMPT = '''
MOTION DIRECTION (required for NEW scripts): set visual_mode="hybrid",
motion_system="editorial-v2" and choose
motion_palette from cobalt, plum, slate to suit THIS story. Each scene has
visual_type: motion, hybrid or video, plus motion:{layout, headline, items, kicker}.
Choose layout to EXPLAIN the story: editor (two to four real layers assemble on a
moving editing timeline; only for a media/editing topic), network (two to four
connected entities), browser (one to four source-grounded interface states),
kinetic (one to three short words become one final composition), compare (two exact
alternatives), workflow (two to four connected steps), terminal (one to four REAL
code/config lines), spotlight (one object or fact, at most two short labels).
headline: at most 60 characters; items: one to four strings, each at most 80
characters; kicker: optional short payoff, at most 85 characters. Use exact source
facts and labels, no invented numbers, fake commands, invented UI statuses or test
results. Preserve original brand spelling and numeric versions ON SCREEN. This
copy is part of the factual review. No stage directions inside visible text.
For editorial-v2 keep headline <=46 characters, items <=44 (kinetic <=24,
spotlight <=28). Use concise labels, never paragraphs. Give the video at least one
pure motion scene and at least one hybrid/video scene.
Use at least two DIFFERENT layouts; no more than two scenes need generated footage.
For example: a physical hook in hybrid, an exact UI/workflow in motion, a kinetic
payoff in motion. Do not make every scene a laptop, a card or an icon with a fade.
Write an edit_note_ru with a concrete INITIAL state, ACTION and RESULT. Movement
continues after the entrance: connect parts, move a playhead, inspect a real detail,
change focus between alternatives. No list of cards fading onto one static ground.
Alternate shot sizes and composition; use no avatars or copied reference assets.
Generated video is illustrative B-roll ONLY; typography, comparisons, diagrams and
exact UI come from the motion compositor. Compose hybrid footage with its subject
in the upper two thirds and keep important objects away from the lower quarter.
Keep the existing English-only video_prompt_en for each scene; it describes the
subject and action, never exact text. Match scene speech and visuals beat for beat.
'''


def validate_direction(script):
    """Legacy saved jobs keep their original visual contract on resume."""
    if script.get('visual_mode') != 'hybrid':
        return script
    if script.get('motion_palette') not in PALETTES:
        raise ValueError('Hybrid video needs a known motion_palette')
    system=script.get('motion_system', VERSION)
    if system not in (VERSION, EDITORIAL_SYSTEM):
        raise ValueError('Unknown motion_system')
    layouts_allowed=EDITORIAL_LAYOUTS if system==EDITORIAL_SYSTEM else LAYOUTS
    types, layouts = [], []
    for index, scene in enumerate(script['scenes'], 1):
        kind = scene.get('visual_type')
        if kind not in ('motion', 'hybrid', 'video'):
            raise ValueError(f'Scene {index}: visual_type must be motion, hybrid or video')
        types.append(kind)
        spec = scene.get('motion')
        if not isinstance(spec, dict) or spec.get('layout') not in layouts_allowed:
            raise ValueError(f'Scene {index}: a supported motion layout is required')
        layouts.append(spec['layout'])
        for key, limit in [('headline', 60), ('kicker', 85)]:
            value = spec.get(key, '')
            if not isinstance(value, str) or len(value) > limit or (key == 'headline' and not value.strip()):
                raise ValueError(f'Scene {index}: invalid motion {key}')
        items = spec.get('items')
        if not isinstance(items, list) or not 1 <= len(items) <= 4 or any(
                not isinstance(v, str) or not v.strip() or len(v) > 80 for v in items):
            raise ValueError(f'Scene {index}: motion items need one to four short strings')
        if spec['layout'] == 'compare' and len(items) != 2:
            raise ValueError('A comparison must have exactly two items')
        if spec['layout'] == 'workflow' and len(items) < 2:
            raise ValueError('A workflow must have at least two steps')
        if system==EDITORIAL_SYSTEM:
            limit={'kinetic':24,'spotlight':28}.get(spec['layout'],44)
            if len(spec['headline'])>46 or any(len(v)>limit for v in items):
                raise ValueError(f'Scene {index}: editorial-v2 needs concise display copy')
            if spec['layout'] in ('spotlight','kinetic') and len(items) > (2 if spec['layout']=='spotlight' else 3):
                raise ValueError(f'Scene {index}: too many foreground objects')
            if spec['layout'] in ('editor','network') and len(items)<2:
                raise ValueError(f'Scene {index}: connected objects need at least two items')
    if 'motion' not in types or not any(t in ('hybrid', 'video') for t in types):
        raise ValueError('Hybrid video must combine motion and fresh footage')
    if sum(t != 'motion' for t in types) > 2 or len(set(layouts)) < 2:
        raise ValueError('Use at most two generated clips and at least two motion layouts')
    # The handle is branding, not something a model may replace with an invented one.
    last = script['scenes'][-1]
    last['motion']['kicker'] = last['on_screen_text_ru']
    return script


def composition(scene, duration, palette='cobalt', system=VERSION):
    if not math.isfinite(duration) or not 1 < duration <= 8:
        raise ValueError('Invalid motion duration')
    if system==EDITORIAL_SYSTEM:
        from editorial_motion import composition as editorial_composition
        return editorial_composition(scene,duration,palette)
    if system!=VERSION:
        raise ValueError('Unknown motion_system')
    spec = scene['motion']
    bg, fg, muted, accent = PALETTES[palette]
    esc = lambda value: html.escape(value, quote=True)
    kind, layout = scene['visual_type'], spec['layout']
    headline = esc(spec['headline'])
    items = ''.join(f'<div class="item"><span>{esc(v)}</span></div>' for v in spec['items'])
    kicker = esc(spec.get('kicker', ''))
    # Hybrid has a clear window onto the independently decoded, never looped clip.
    return f'''<!doctype html><html lang="ru"><meta charset="utf-8">
<style>
@font-face{{font-family:Motion;src:url("MotionSans.ttf")}}*{{box-sizing:border-box}}
html,body{{margin:0;width:720px;height:1280px;overflow:hidden;background:transparent}}
body{{font-family:Motion,sans-serif;color:{fg};--accent:{accent};--muted:{muted}}}
#stage{{position:absolute;inset:0;background:{bg};overflow:hidden}}
#stage.hybrid{{background:transparent}}
.hybrid .sheet{{position:absolute;top:600px;left:0;right:0;bottom:0;background:{bg};border-radius:46px 46px 0 0}}
.hybrid .headline{{top:650px;font-size:40px;max-height:125px}}
.hybrid .visual{{top:800px;height:160px}}
.hybrid .kicker{{top:978px;font-size:24px}}
.headline{{position:absolute;left:72px;right:100px;top:150px;font-size:66px;font-weight:bold;line-height:1.07;letter-spacing:-2px;overflow-wrap:anywhere}}
.visual{{position:absolute;left:72px;right:100px;top:430px;height:420px}}
.item{{position:relative;line-height:1.18;overflow-wrap:anywhere}}
.kinetic .item{{font-size:60px;font-weight:bold;margin-bottom:16px}}
.kinetic .item:nth-child(even){{color:var(--accent);padding-left:32px}}
.compare .visual{{display:flex;align-items:stretch;gap:20px}}
.compare .item{{flex:1;padding:32px 20px;background:{fg};color:{bg};border-radius:24px;font-size:38px;display:flex;align-items:center}}
.compare .item+ .item{{background:var(--accent);align-self:flex-end;min-height:70%}}
.workflow .item{{padding:25px 28px 25px 56px;font-size:35px;border-left:5px solid var(--accent)}}
.workflow .item:before{{content:"";position:absolute;left:-12px;top:35px;width:19px;height:19px;border-radius:50%;background:var(--accent)}}
.terminal .visual{{background:#101a28;border-radius:22px;padding:34px 22px;border-top:10px solid var(--accent)}}
.terminal .item{{font-family:monospace;font-size:28px;color:#d2ecf6;margin:22px 0;white-space:pre-wrap}}
.terminal .item:before{{content:"> ";color:var(--accent)}}
.spotlight .visual{{display:flex;flex-direction:column;justify-content:center}}
.spotlight .item{{font-size:86px;font-weight:bold;line-height:1.05;color:var(--accent);margin-bottom:24px}}
.hybrid .item{{font-size:31px;margin-bottom:8px;padding-top:10px;padding-bottom:10px}}
.hybrid.spotlight .item{{font-size:46px}}
.hybrid.terminal .visual{{padding:16px}}
.hybrid.terminal .item{{font-size:24px}}
.kicker{{position:absolute;top:910px;left:72px;right:100px;font-size:30px;line-height:1.2;color:var(--muted);overflow-wrap:anywhere}}
.line{{position:absolute;left:72px;right:100px;top:112px;height:7px;background:var(--accent);transform-origin:left}}
.hybrid .line{{top:630px;left:72px;right:570px;height:5px}}
.wipe{{position:absolute;inset:0;background:var(--accent);transform:translateX(-100%)}}
</style>
<div id="stage" class="{kind} {layout}" data-composition-id="scene" data-duration="{duration:.6f}" data-width="720" data-height="1280">
<div class="sheet"></div><div class="line"></div><div class="headline">{headline}</div>
<div class="visual">{items}</div><div class="kicker">{kicker}</div><div class="wipe"></div></div>
<script src="gsap.min.js"></script><script>
const duration={duration:.6f};
const tl=gsap.timeline({{paused:true}});
tl.fromTo('.line',{{scaleX:0}},{{scaleX:1,duration:.5,ease:'power3.out'}},0);
tl.fromTo('.headline',{{y:55,opacity:0}},{{y:0,opacity:1,duration:.48,ease:'power3.out'}},0);
const layout={json.dumps(layout)};
const enter=Math.min(.48,duration*.14),base=Math.min(.4,duration*.1);
const step=Math.max(.08,(duration*.6-base)/Math.max(1,document.querySelectorAll('.item').length-1));
if(layout==='kinetic'){{
 tl.fromTo('.item',{{x:160,rotation:3,opacity:0}},{{x:0,rotation:0,opacity:1,duration:enter,stagger:step,ease:'back.out(1.15)'}},base);
 tl.to('.visual',{{scale:1.025,duration:Math.max(.1,duration-2),ease:'none'}},1.5);
}}else if(layout==='compare'){{
 tl.fromTo('.item',{{y:160,opacity:0}},{{y:0,opacity:1,stagger:step,duration:enter,ease:'power4.out'}},base);
 tl.fromTo('.item:last-child',{{rotation:4}},{{rotation:0,duration:enter}},base+step);
}}else if(layout==='workflow'){{
 tl.fromTo('.item',{{x:-45,opacity:0}},{{x:0,opacity:1,stagger:step,duration:enter,ease:'power2.out'}},base);
}}else if(layout==='terminal'){{
 tl.fromTo('.visual',{{scale:.91,opacity:0}},{{scale:1,opacity:1,duration:.55,ease:'power3.out'}},.2);
 tl.fromTo('.item',{{x:20,opacity:0}},{{x:0,opacity:1,stagger:step,duration:.2}},base);
}}else{{
 tl.fromTo('.item',{{scale:.72,opacity:0}},{{scale:1,opacity:1,stagger:step,duration:enter,ease:'back.out(1.3)'}},base);
}}
tl.fromTo('.kicker',{{y:20,opacity:0}},{{y:0,opacity:1,duration:.4}},Math.min(duration-.55,Math.max(1.2,duration*.7)));
tl.to('.wipe',{{x:'200%',duration:.26,ease:'power2.inOut'}},Math.max(0,duration-.26));
window.__timelines={{scene:tl}};
// A GSAP timeline is thenable: returning it makes Playwright await a paused film.
window.seek=t=>{{tl.seek(Math.max(0,Math.min(t,duration)),true);return true;}};
window.seek(0);
window.fitText=()=>{{
 const visual=document.querySelector('.visual');
 for(let i=0;i<12&&visual.scrollHeight>visual.clientHeight+2;i++){{
  document.querySelectorAll('.item').forEach(e=>e.style.fontSize=(parseFloat(getComputedStyle(e).fontSize)*.93)+'px');
 }}
}};
window.layoutProblems=()=>{{
 const visual=document.querySelector('.visual'),head=document.querySelector('.headline'),kicker=document.querySelector('.kicker');
 const bad=Array.from(document.querySelectorAll('.headline,.item,.kicker')).filter(e=>{{const r=e.getBoundingClientRect();return r.right>640||r.left<35||r.bottom>1180||e.scrollWidth>e.clientWidth+2}}).map(e=>e.textContent);
 if(head.getBoundingClientRect().bottom>visual.getBoundingClientRect().top-10)bad.push('headline overlaps visual');
 if(visual.scrollHeight>visual.clientHeight+2)bad.push('visual overflow');
 if(visual.getBoundingClientRect().bottom>kicker.getBoundingClientRect().top-10)bad.push('visual overlaps kicker');
 return bad;
}};
</script></html>'''


def render(scene, audio, subtitles, duration, target, palette='cobalt', raw=None, system=VERSION):
    """Render inside the existing media container, under the shared heavy-work lease."""
    from autonomous_video_pipeline import BASE_DIR, container_path, probe
    from browser_process import render_lease, run
    if scene['visual_type'] == 'hybrid' and raw is None:
        raise ValueError('Hybrid scene needs its own fresh generated clip')
    if raw is not None and float(probe(raw)['format']['duration']) + .05 < duration:
        raise RuntimeError('New clip is shorter than narration; no looping or freezing')
    target = Path(target)
    project = target.parent / (target.stem + '_motion')
    project.mkdir(exist_ok=True)
    source = composition(scene, duration, palette,system)
    (project / 'index.html').write_text(source)
    assets = Path(__file__).parent / 'motion_assets'
    asset_names=('gsap.min.js','MotionSans.ttf','MotionDisplay.ttf','Manrope-OFL.txt') if system==EDITORIAL_SYSTEM else ('gsap.min.js','MotionSans.ttf')
    for name in asset_names:
        content = (assets / name).read_bytes()
        (project / name).write_bytes(content)
    command = ['docker','exec','video_factory_auth','python3','-u','/app/scripts/motion_capture.py',
        container_path(project / 'index.html'),container_path(audio),container_path(subtitles),
        str(duration),container_path(target)]
    if raw is not None: command += ['--raw',container_path(raw)]
    if system==EDITORIAL_SYSTEM: command += ['--native-captions']
    with render_lease(BASE_DIR):
        result=run(command,timeout=660,capture_output=True,text=True)
    if result.returncode:
        raise RuntimeError('Motion render failed: '+(result.stderr or result.stdout)[-2000:])
    metadata=json.loads((project/'render.json').read_text())
    digest=hashlib.sha256(target.read_bytes()).hexdigest()
    return {'engine':system,'composition':str(project/'index.html'),
        'composition_sha256':hashlib.sha256(source.encode()).hexdigest(),
        'render_sha256':digest,'capture':metadata}
