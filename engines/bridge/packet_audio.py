"""Deadline-based speaker packets: Opus, previous-frame redundancy and AES-GCM.

The key is sent only through authenticated SSH stdin. Each new receiver uses a
fresh key/salt. Sequence numbers never wrap, and the full header is authenticated.
No retry or acknowledgement can hold up the next audio frame.
"""
import ctypes as C
import ctypes.util
import socket
import struct

FRAME_BYTES = 3840  # stereo S16LE, 48 kHz, 20 ms
MAX_OPUS = 400


def library(name):
    path = ctypes.util.find_library(name)
    if not path:
        raise RuntimeError(f'Missing system library: lib{name}')
    return C.CDLL(path)


def function(lib, name, result, *args):
    fn = getattr(lib, name)
    fn.restype, fn.argtypes = result, list(args)
    return fn


class PacketEncoder:
    def __init__(self, secret):
        if len(secret) != 40:
            raise ValueError('Expected a 32-byte key and 8-byte session salt')
        self.key, self.salt = secret[:32], secret[32:]
        self.previous = b''
        self.previous_sequence = None
        self.opus = library('opus')
        self.crypto = library('crypto')
        ptr, integer = C.c_void_p, C.c_int
        self.create = function(self.opus, 'opus_encoder_create', ptr, integer, integer,
                               integer, C.POINTER(integer))
        self.destroy = function(self.opus, 'opus_encoder_destroy', None, ptr)
        # The last argument to this API is variadic, so declare only its prefix.
        self.ctl = function(self.opus, 'opus_encoder_ctl', integer, ptr, integer)
        self.encode = function(self.opus, 'opus_encode', integer, ptr, ptr, integer,
                               ptr, integer)
        self.ctx_new = function(self.crypto, 'EVP_CIPHER_CTX_new', ptr)
        self.ctx_free = function(self.crypto, 'EVP_CIPHER_CTX_free', None, ptr)
        self.cipher = function(self.crypto, 'EVP_aes_256_gcm', ptr)
        self.init = function(self.crypto, 'EVP_EncryptInit_ex', integer, ptr, ptr,
                             ptr, ptr, ptr)
        self.update = function(self.crypto, 'EVP_EncryptUpdate', integer, ptr, ptr,
                               C.POINTER(integer), ptr, integer)
        self.final = function(self.crypto, 'EVP_EncryptFinal_ex', integer, ptr, ptr,
                              C.POINTER(integer))
        self.ctrl = function(self.crypto, 'EVP_CIPHER_CTX_ctrl', integer, ptr,
                             integer, integer, ptr)
        error = integer()
        self.encoder = self.create(48000, 2, 2049, C.byref(error))  # AUDIO
        if not self.encoder or error.value:
            raise RuntimeError('Cannot initialize Opus encoder')
        try:
            for request, value in [(4002, 64000), (4006, 0), (4010, 5)]:
                if self.ctl(self.encoder, request, integer(value)):
                    raise RuntimeError('Cannot configure Opus encoder')
        except BaseException:
            self.close()
            raise

    def close(self):
        if self.encoder:
            self.destroy(self.encoder)
            self.encoder = None

    def packet(self, pcm, sequence):
        if len(pcm) != FRAME_BYTES or not 0 <= sequence < 2**32:
            raise ValueError('Invalid audio frame or exhausted session sequence')
        if self.previous_sequence is not None and sequence <= self.previous_sequence:
            raise ValueError('Packet sequence must increase; never reuse a nonce')
        encoded = C.create_string_buffer(MAX_OPUS)
        length = self.encode(self.encoder, pcm, 960, encoded, MAX_OPUS)
        if length <= 0:
            raise RuntimeError(f'Opus encoding failed: {length}')
        current = encoded.raw[:length]
        previous = self.previous if self.previous_sequence == sequence - 1 else b''
        body = struct.pack('!HH', len(current), len(previous)) + current + previous
        header = b'AVP1' + self.salt + struct.pack('!I', sequence)
        ctx = self.ctx_new()
        if not ctx:
            raise RuntimeError('Cannot allocate AES-GCM context')
        output = C.create_string_buffer(len(body) + 16)
        tag = C.create_string_buffer(16)
        size, tail = C.c_int(), C.c_int()
        try:
            if not (self.init(ctx, self.cipher(), None, self.key, header[4:]) == 1
                    and self.update(ctx, None, C.byref(size), header, len(header)) == 1
                    and self.update(ctx, output, C.byref(size), body, len(body)) == 1
                    and self.final(ctx, C.byref(output, size.value), C.byref(tail)) == 1
                    and self.ctrl(ctx, 0x10, 16, tag) == 1):  # GET_TAG
                raise RuntimeError('AES-GCM encryption failed')
        finally:
            self.ctx_free(ctx)
        self.previous = current
        self.previous_sequence = sequence
        return header + output.raw[:len(body)] + tag.raw


def sender_socket(address):
    """A connected socket keeps replies/ICMP scoped to the chosen station."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(address)
        sock.setblocking(False)
        return sock
    except BaseException:
        sock.close()
        raise
