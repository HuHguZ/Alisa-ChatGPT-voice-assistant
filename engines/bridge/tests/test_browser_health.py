"""Challenge/limit notices must not be inferred from conversation contents."""
import html,json,re,shutil,subprocess,tempfile,unittest
from pathlib import Path
from browser_control import Browser

@unittest.skipUnless(shutil.which('chromium'),'Chromium is needed to render the local DOM fixture')
class Health(unittest.IsolatedAsyncioTestCase):
 async def health(self,title,body,notices=()):
  browser=Browser()
  async def evaluate(expression):
   with tempfile.TemporaryDirectory(prefix='alisa-dom-test-') as folder:
    page=Path(folder)/'fixture.html'
    markup='<!doctype html><title>'+html.escape(title)+'</title><body>'+html.escape(body)+''.join('<div role="alert">'+html.escape(n)+'</div>' for n in notices)+'<pre id="health-json"></pre><script>document.querySelector("#health-json").textContent=JSON.stringify('+expression+');</script>'
    page.write_text(markup)
    result=subprocess.run(['chromium','--headless','--disable-gpu','--disable-background-networking','--no-first-run',
                           '--user-data-dir='+str(Path(folder)/'profile'),'--dump-dom',page.as_uri()],capture_output=True,check=True,timeout=20)
    match=re.search(r'<pre id="health-json">(.*?)</pre>',result.stdout.decode(),re.S)
    return json.loads(html.unescape(match.group(1)))
  browser.evaluate=evaluate
  return await browser.health()
 async def test_empty_challenge_page_uses_its_title(self):
  value=await self.health('Just a moment...','')
  self.assertTrue(value['verification']);self.assertFalse(value['quota'])
 async def test_russian_challenge_title_without_visible_body_is_detected(self):
  value=await self.health('Один момент…','')
  self.assertTrue(value['verification']);self.assertFalse(value['quota'])
 async def test_conversation_about_voice_limits_is_not_a_limit(self):
  value=await self.health('ChatGPT','Explain Cloudflare and voice limits. Try again later is an error message.')
  self.assertFalse(value['verification']);self.assertFalse(value['quota'])
 async def test_visible_limit_notice_is_reported(self):
  value=await self.health('ChatGPT','',('You have reached your daily voice limit',))
  self.assertTrue(value['quota'])
