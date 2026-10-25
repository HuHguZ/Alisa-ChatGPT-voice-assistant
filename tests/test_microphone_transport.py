"""Backpressure must drop stale input instead of delaying the next utterance."""
import asyncio
import contextlib
import fcntl
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from microphone_transport import BLOCK_BYTES, MicrophoneTransport, limit_input_pipe


class Microphone(unittest.IsolatedAsyncioTestCase):
    async def test_burst_keeps_only_the_fresh_tail_and_counts_drops(self):
        state = {'capture_bytes': 0}
        stream = MicrophoneTransport(state, lambda _: 0)
        source = asyncio.StreamReader()
        blocks = [bytes([i]) * BLOCK_BYTES for i in range(20)]
        source.feed_data(b''.join(blocks))
        source.feed_eof()
        with self.assertRaises(asyncio.IncompleteReadError):
            await stream.read(source)
        self.assertEqual(state['capture_bytes'], BLOCK_BYTES * 20)
        self.assertEqual(state['microphone_queue_ms'], 60)
        self.assertEqual(state['microphone_dropped_bytes'], BLOCK_BYTES * 14)
        self.assertEqual([stream.queue.get_nowait()[1] for _ in range(6)], blocks[-6:])

    async def test_expired_audio_is_not_written_after_a_stall(self):
        now = [0.0]
        state = {'capture_bytes': 0}
        stream = MicrophoneTransport(state, lambda _: 0, clock=lambda: now[0])
        stream.queue.put_nowait((0.0, b'a' * BLOCK_BYTES))
        now[0] = .2
        stream.queue.put_nowait((.2, b'b' * BLOCK_BYTES))
        received, ready = [], asyncio.Event()

        class Destination:
            def write(self, block):
                received.append(block)

            async def drain(self):
                ready.set()

        task = asyncio.create_task(stream.write(Destination()))
        try:
            await asyncio.wait_for(ready.wait(), 1)
            self.assertEqual(received, [b'b' * BLOCK_BYTES])
            self.assertEqual(state['microphone_dropped_bytes'], BLOCK_BYTES)
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    async def test_real_child_pipe_has_a_small_capacity(self):
        proc = await asyncio.create_subprocess_exec(
            sys.executable, '-c', 'import sys; sys.stdin.buffer.read()',
            stdin=asyncio.subprocess.PIPE)
        try:
            self.assertLessEqual(limit_input_pipe(proc.stdin), 4096)
            pipe = proc.stdin.transport.get_extra_info('pipe')
            self.assertLessEqual(fcntl.fcntl(pipe.fileno(), fcntl.F_GETPIPE_SZ), 4096)
            self.assertEqual(proc.stdin.transport.get_write_buffer_limits(), (0, BLOCK_BYTES * 2))
        finally:
            proc.stdin.close()
            await asyncio.wait_for(proc.wait(), 3)
