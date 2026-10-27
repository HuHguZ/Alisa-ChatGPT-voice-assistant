"""Exercise the Python controller with the real C agent and a fake browser."""
import asyncio
import json
import os
from pathlib import Path
import signal
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import voice
from native_control import fields
from test_native_agent import state_frame
from test_native_focus import focus_event
from audio_focus import silent_lease


class Lifecycle(unittest.IsolatedAsyncioTestCase):
    async def test_repeated_native_and_web_closes_restore_alice_without_duplicate_cancel(self):
        capture = False
        connected = asyncio.Event()
        methods = []
        pauses = []
        fail_focus = False
        last_focus_request = None
        peer = None

        async def receive(reader, writer):
            nonlocal peer, last_focus_request
            peer = writer
            connected.set()
            try:
                while True:
                    size = int.from_bytes(await reader.readexactly(4), 'little')
                    remote = fields(fields(await reader.readexactly(size))[252])
                    if remote.get(7) == b'AliceCapability':
                        methods.append(fields(remote[8])[1])
                        writer.write(state_frame(0))  # Cancel completes stock input.
                        await writer.drain()
                    elif remote.get(7) == b'PlaybackControlCapability':
                        pauses.append(fields(remote[13])[1])
                    elif remote.get(7) == b'AudioFilePlayerCapability':
                        method = fields(remote[12])
                        if method[1] == 0:
                            last_focus_request = fields(method[4])[4]
                            writer.write(focus_event(last_focus_request,
                                                     completed=1 if fail_focus else None))
                            await writer.drain()
            except asyncio.IncompleteReadError:
                pass
            finally:
                writer.close()
                await writer.wait_closed()

        class Process:
            def poll(self):
                return None

        class Browser:
            background = True
            last_url = 'https://chatgpt.com/c/01234567-1234-5678-9abc-0123456789ab'
            proc = None

            async def launch(self):
                nonlocal capture
                self.proc = Process()
                capture = True

            async def close(self):
                nonlocal capture
                self.proc = None
                capture = False

            async def resume_previous(self):
                pass

            async def remember_current(self):
                pass


        async def until(predicate):
            async with asyncio.timeout(6):
                while not predicate():
                    await asyncio.sleep(.02)

        server = await asyncio.start_server(receive, '127.0.0.1', 0)
        self.addAsyncCleanup(server.wait_closed)
        self.addCleanup(server.close)
        port = server.sockets[0].getsockname()[1]
        for sig in [signal.SIGINT, signal.SIGTERM]:
            self.addCleanup(signal.signal, sig, signal.getsignal(sig))
            self.addCleanup(asyncio.get_running_loop().remove_signal_handler, sig)
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            (state/'template.wav').write_bytes(silent_lease())
            config = {'host': 'fake', 'phase': 'running', 'pid': os.getpid()}
            command = ['/usr/bin/env', f'LD_PRELOAD={ROOT}/build/native_agent.host.so',
                       'ALICE_NATIVE_AGENT=1', f'ALICE_NATIVE_PORT={port}',
                       'ALICE_NATIVE_FOCUS=1',
                       f'ALICE_FOCUS_TEMPLATE={state}/template.wav',
                       f'ALICE_FOCUS_DIRECTORY={state}',
                       f'ALICE_NATIVE_LOCK={state}/agent.lock', '/usr/bin/true']
            with patch.multiple(voice, STATE_DIR=state, STATE=state/'voice.json',
                                SOCKET=state/'voice.sock', Browser=Browser), \
                 patch.object(voice, 'read_state', lambda: {**config, 'native_alice_paused': capture}), \
                 patch.object(voice, 'alive', return_value=True), \
                 patch.object(voice, 'ssh_args', return_value=command), \
                 patch.object(voice, 'prepare_browser_audio', lambda: capture), \
                 patch.object(voice, 'play_closed', new_callable=AsyncMock) as cue:
                task = asyncio.create_task(voice.run())
                try:
                    await asyncio.wait_for(connected.wait(), 3)

                    def status():
                        try:
                            return json.loads((state/'voice.json').read_text())
                        except (FileNotFoundError, ValueError):
                            return {}

                    await until(lambda: status().get('native_events_connected'))
                    for index, action in enumerate(['open', 'close', 'open', 'close'], 1):
                        await asyncio.sleep(2.1 if index > 1 else 0)
                        request = f'00000000-0000-4000-8000-{index:012d}'
                        phrase = 'открой чат' if action == 'open' else 'закрой чат'
                        peer.write(state_frame(1, request=request))
                        peer.write(state_frame(3, phrase, request=request, final=True))
                        await peer.drain()
                        await until(lambda: status().get('successful_actions') == index)
                        self.assertEqual(status()['last_action'], action)
                        await until(lambda: len(methods) >= index)
                        self.assertEqual(methods, [6, 3, 6, 3][:index])
                        self.assertEqual(capture, action == 'open')
                        self.assertEqual(pauses, [1]*((index+1)//2))
                    self.assertEqual(cue.await_count, 2)
                    await asyncio.sleep(2.1)
                    peer.write(state_frame(3, 'открой чат',
                        request='00000000-0000-4000-8000-000000000005', final=True))
                    await peer.drain()
                    await until(lambda: status().get('successful_actions') == 5)
                    capture = False  # The call ends, but Chromium is still alive.
                    await until(lambda: status().get('command_source') == 'web-call-ended')
                    self.assertEqual(status()['last_action'], 'close')
                    self.assertFalse(capture)
                    await until(lambda: len(methods) >= 6)
                    self.assertEqual(methods, [6, 3, 6, 3, 6, 3])
                    self.assertEqual(cue.await_count, 3)
                    # Failure to acquire focus must never open the browser.
                    await asyncio.sleep(2.1)
                    fail_focus = True
                    peer.write(state_frame(3, 'открой чат',
                        request='00000000-0000-4000-8000-000000000006', final=True))
                    await peer.drain()
                    await until(lambda: 'audio focus' in status().get('error', ''))
                    self.assertFalse(capture)
                    self.assertEqual(status()['successful_actions'], 6)
                    # Unexpected loss during an established call closes it.
                    fail_focus = False
                    await asyncio.sleep(2.1)
                    peer.write(state_frame(3, 'открой чат',
                        request='00000000-0000-4000-8000-000000000007', final=True))
                    await peer.drain()
                    await until(lambda: status().get('successful_actions') == 7)
                    self.assertTrue(capture)
                    peer.write(focus_event(last_focus_request, focused=False))
                    await peer.drain()
                    await until(lambda: status().get('command_source') == 'native-focus-lost')
                    self.assertFalse(capture)
                finally:
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
