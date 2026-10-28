#!/usr/bin/env python3
"""Validate the dedicated ordinary browser; do not export session tokens."""
import argparse,asyncio
from browser_native_control import Browser

async def verify(profile):
    browser=Browser();browser.profile=profile
    try:
        await browser.launch()
        for _ in range(30):
            health=await browser.health()
            if health.get('verification'):raise RuntimeError('browser_verification_required')
            if health.get('buttons',0)>0:
                valid=await browser.evaluate("fetch('/api/auth/session',{credentials:'include'}).then(r=>r.json()).then(s=>!!s.accessToken).catch(()=>false)")
                if valid:return
            await asyncio.sleep(.5)
        raise RuntimeError('login_required')
    finally:await browser.close()

if __name__=='__main__':
    from pathlib import Path
    parser=argparse.ArgumentParser();parser.add_argument('--profile',type=Path,required=True);args=parser.parse_args()
    try:asyncio.run(verify(args.profile.resolve()));print('Browser login verified; no tokens exported.')
    except Exception:print('Browser login could not be verified.');raise SystemExit(1)
