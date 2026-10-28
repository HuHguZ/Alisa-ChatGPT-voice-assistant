"""Keep live microphone input fresh instead of buffering seconds of speech."""
import asyncio
import fcntl
import time

BLOCK_BYTES = 960  # 10 ms of mono S16_LE at 48 kHz.


def limit_input_pipe(writer, block_bytes=BLOCK_BYTES):
    """Bound both the Linux pipe and asyncio's otherwise 64 KiB write buffer."""
    pipe = writer.transport.get_extra_info('pipe')
    capacity = fcntl.fcntl(pipe.fileno(), fcntl.F_SETPIPE_SZ, 4096)
    writer.transport.set_write_buffer_limits(high=block_bytes * 2, low=0)
    return capacity


class MicrophoneTransport:
    def __init__(self, state, level, clock=time.monotonic, sample_rate=48000):
        if sample_rate not in (16000, 48000):
            raise ValueError('Microphone rate must be 16000 or 48000')
        self.block_bytes = sample_rate // 100 * 2
        self.state = state
        self.level = level
        self.clock = clock
        self.queue = asyncio.Queue(maxsize=6)
        state.update(microphone_dropped_bytes=0, microphone_queue_ms=0)

    def discard(self):
        self.state['microphone_dropped_bytes'] += self.block_bytes

    async def read(self, source):
        while True:
            block = await source.readexactly(self.block_bytes)
            self.state['capture_bytes'] += len(block)
            self.state['microphone_rms'] = round(self.level(block), 6)
            if self.queue.full():
                self.queue.get_nowait()
                self.discard()
            self.queue.put_nowait((self.clock(), block))
            self.state['microphone_queue_ms'] = self.queue.qsize() * 10

    async def write(self, destination):
        while True:
            queued_at, block = await self.queue.get()
            self.state['microphone_queue_ms'] = self.queue.qsize() * 10
            if self.clock() - queued_at > .12:
                self.discard()
                continue
            destination.write(block)
            await asyncio.wait_for(destination.drain(), 3)
