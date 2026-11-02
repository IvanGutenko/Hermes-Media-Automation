import asyncio,fcntl,hashlib,json,re,time,sys
from pathlib import Path
sys.path.insert(0,'/app/scripts')
from browser_session import connect
from playwright.async_api import async_playwright
ROOT=Path('/data/output')
run='migratoria_sanya_20261028_s1_vids'
doc='https://docs.google.com/videos/d/1uwGnw5gtVeW1EClUvwlA8uQoVgMGPv0UZMXi0nOJbRI/edit'
prompt='''[Cut 1: Hook] Vertical cinematic shot of an empty Siberian airport apron at blue hour, falling snow, a modern passenger jet taxiing safely in the background. Camera pushes towards a closed terminal window, luggage standing beside it. At one and a half seconds, hard cut to a matching passenger jet descending over Sanya's tropical coast with turquoise water and palm-lined bays. Both shots established within three seconds. Realistic cinematic geography, no map or text, no people, no logos, no talking characters. Silent footage; no voice, dialogue or singing.'''
receipt=ROOT/('vids_'+run+'.json')
def save(state):
 temp=receipt.with_suffix('.tmp');temp.write_text(json.dumps(state,ensure_ascii=False,indent=2));temp.replace(receipt)
async def main():
 with (ROOT/'.flow-generation.lock').open('a') as lock:
  fcntl.flock(lock,fcntl.LOCK_EX)
  if receipt.exists():raise RuntimeError('Receipt already exists: observe the existing request instead of resubmitting')
  async with async_playwright() as p:
   b=await connect(p);page=next(x for x in b.contexts[0].pages if doc in x.url);page.set_default_timeout(15000)
   await page.bring_to_front();await page.keyboard.press('Escape')
   settings=page.get_by_role('button',name=re.compile('^Настройки генерации:'))
   await settings.click()
   await page.get_by_role('radio',name='Вертикальное',exact=True).click()
   await page.get_by_role('slider',name='Длина видео',exact=True).fill('8')
   await settings.click()
   config=await settings.get_attribute('aria-label')
   if 'Вертикальное, 8 с' not in config:raise RuntimeError('Wrong generation settings')
   box=page.get_by_role('textbox',name=re.compile('^Опишите видео'))
   await box.fill(prompt)
   if (await box.inner_text()).strip()!=prompt:raise RuntimeError('Prompt was not retained')
   baseline=await page.locator('video[src]').evaluate_all('(es)=>es.map(e=>e.src)')
   state={'run_id':run,'provider':'vids','status':'submission_attempted','prompt':prompt,
          'prompt_sha256':hashlib.sha256(prompt.encode()).hexdigest(),'document_url':doc,'account':1,
          'started_at':time.time(),'settings':config,'baseline_video_sources':baseline}
   save(state)
   await page.get_by_role('button',name='Сгенерировать',exact=True).click()
   state.update(status='submitted',submitted_at=time.time());save(state)
   await page.wait_for_timeout(20000)
   print('body',(await page.locator('body').inner_text())[-5000:],flush=True)
   print('videos',await page.locator('video').evaluate_all('(es)=>es.map(e=>({src:e.src,duration:e.duration}))'),flush=True)
asyncio.run(main())
