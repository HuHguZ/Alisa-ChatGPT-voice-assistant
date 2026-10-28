"""Only the ordinary Voice introduction may be continued automatically."""
import asyncio,html,json,re,shutil,subprocess,tempfile,unittest
from pathlib import Path
from unittest.mock import AsyncMock
from browser_control import Browser

@unittest.skipUnless(shutil.which('chromium'),'Chromium DOM fixture required')
class VoiceIntro(unittest.IsolatedAsyncioTestCase):
 async def exercise(self,markup):
  browser=Browser();browser.command=AsyncMock(return_value={})
  async def evaluate(expression):
   with tempfile.TemporaryDirectory(prefix='alisa-intro-') as folder:
    path=Path(folder)/'intro.html'
    path.write_text('<!doctype html><body>'+markup+'<pre id="result"></pre><script>document.querySelector("#result").textContent=JSON.stringify('+expression+');</script>')
    result=subprocess.run(['chromium','--headless','--disable-gpu','--no-first-run','--user-data-dir='+str(Path(folder)/'profile'),'--dump-dom',path.as_uri()],capture_output=True,check=True,timeout=20)
    text=re.search(r'<pre id="result">(.*?)</pre>',result.stdout.decode(),re.S).group(1)
    return json.loads(html.unescape(text))
  browser.evaluate=evaluate
  return await browser.click_voice(),browser.command
 async def test_first_voice_introduction_is_completed(self):
  clicked,command=await self.exercise('<div role="dialog"><h2>Meet Voice</h2><p>Say what is on your mind.</p><button>Continue</button></div>')
  self.assertTrue(clicked);self.assertEqual(command.call_args_list[0].args[0],'Browser.grantPermissions');self.assertEqual(command.await_count,3)
 async def test_optional_recording_sharing_is_not_accepted(self):
  clicked,command=await self.exercise('<div role="dialog"><h2>Share your recordings</h2><p>Improve models.</p><button>Continue</button></div>')
  self.assertFalse(clicked);command.assert_not_awaited()
 async def test_account_with_intro_already_completed_opens_voice_directly(self):
  clicked,command=await self.exercise('<button aria-label="Start Voice">Voice</button>')
  self.assertTrue(clicked);self.assertEqual(command.await_count,3)
 async def test_missing_voice_button_does_not_click_unrelated_continue(self):
  clicked,command=await self.exercise('<button>Continue</button>')
  self.assertFalse(clicked);command.assert_not_awaited()
 async def test_first_voice_selection_uses_current_selection(self):
  clicked,command=await self.exercise('<div role="dialog"><h2>Choose your voice</h2><button>Start Voice</button></div>')
  self.assertTrue(clicked);self.assertEqual(command.await_count,3)
