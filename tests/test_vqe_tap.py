"""Exercise the processed microphone hook against a synthetic VQE ABI fixture."""
import array
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class VqeTap(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='alice-vqe-fixture-')
        cls.root = Path(cls.temp.name)
        (cls.root/'original.c').write_text(r'''
#include <stddef.h>
#include <errno.h>
static float samples[357];
const float *fixture_samples(void) {
    const float pattern[] = {-2,-1,-.5,0,.5,1,2,__builtin_nanf(""),
                            __builtin_inff(),-__builtin_inff()};
    for (unsigned int i=0; i<357; ++i) samples[i]=pattern[i%10];
    return samples;
}
void *YandexVqeC_GetOutputChannelData(void *vqe, unsigned int kind,
        unsigned int index, const float **data, size_t *frames) {
    if (index == 99) { *data=0; *frames=0; errno=17; return (void *)0x1234; }
    *data=fixture_samples(); *frames=357; errno=37; return 0;
}
''')
        (cls.root/'driver.c').write_text(r'''
#include <stddef.h>
#include <stdlib.h>
#include <string.h>
#include <errno.h>
extern const float *fixture_samples(void);
extern void *YandexVqeC_GetOutputChannelData(void *,unsigned int,unsigned int,
                                          const float **,size_t *);
int main(int argc, char **argv) {
    float before[357]; const float *original=fixture_samples();
    memcpy(before,original,sizeof(before));
    unsigned int count=argc>1?(unsigned int)atoi(argv[1]):1;
    for(unsigned int i=0;i<count;++i) {
        const float *data=0; size_t frames=0;
        void *error=YandexVqeC_GetOutputChannelData(0,1,0,&data,&frames);
        if(error||data!=original||frames!=357||errno!=37)return 1;
        error=YandexVqeC_GetOutputChannelData(0,6,1,&data,&frames);
        if(error||data!=original||frames!=357||errno!=37)return 2;
        error=YandexVqeC_GetOutputChannelData(0,6,99,&data,&frames);
        if(error!=(void *)0x1234||data||frames||errno!=17)return 3;
        error=YandexVqeC_GetOutputChannelData(0,6,0,&data,&frames);
        if(error||data!=original||frames!=357||errno!=37)return 4;
        if(memcmp(before,original,sizeof(before)))return 5;
    }
    return 0;
}
''')
        subprocess.run(['clang', '-shared', '-fPIC', str(cls.root/'original.c'),
                        '-o', str(cls.root/'libfixture.so')], check=True)
        subprocess.run(['clang', str(cls.root/'driver.c'), '-L'+str(cls.root),
                        '-lfixture', '-Wl,-rpath,'+str(cls.root),
                        '-o', str(cls.root/'driver')], check=True)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def invoke(self, path, count=1):
        env = {**os.environ, 'LD_PRELOAD': str(ROOT/'build/vqe_tap.host.so')}
        if path is not None:
            env['ALICE_CAPTURE_SOCKET'] = str(path)
        else:
            env.pop('ALICE_CAPTURE_SOCKET', None)
        return subprocess.run([str(self.root/'driver'), str(count)], env=env,
                              capture_output=True, timeout=3)

    def test_only_main_processed_channel_is_copied_with_correct_scaling(self):
        with tempfile.TemporaryDirectory(prefix='alice-vqe-receiver-') as temp:
            path = Path(temp)/'mic.sock'
            with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as receiver:
                receiver.bind(str(path))
                receiver.settimeout(.1)
                proc = self.invoke(path)
                self.assertEqual(proc.returncode, 0, proc.stderr.decode())
                self.assertIn(b'channel=vqe_0 type=6 index=0 rate=16000', proc.stderr)
                packets = [receiver.recv(4096) for _ in range(3)]
                self.assertEqual([len(p) for p in packets], [320, 320, 74])
                with self.assertRaises(TimeoutError):
                    receiver.recv(4096)  # No raw, secondary or failed outputs.
            actual = array.array('h')
            actual.frombytes(b''.join(packets))
            pattern = [-32768, -32768, -16384, 0, 16384, 32767, 32767, 0, 32767, -32768]
            self.assertEqual(list(actual), [pattern[i % 10] for i in range(357)])

    def test_absent_or_full_receiver_cannot_stall_or_change_native_audio(self):
        with tempfile.TemporaryDirectory(prefix='alice-vqe-backpressure-') as temp:
            path = Path(temp)/'mic.sock'
            for receiver_exists in (False, True):
                with self.subTest(receiver_exists=receiver_exists):
                    with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as receiver:
                        if receiver_exists:
                            receiver.bind(str(path))
                        proc = self.invoke(path, count=5000)
                        self.assertEqual(proc.returncode, 0, proc.stderr.decode())

    def test_no_socket_configuration_leaves_the_original_function_alone(self):
        proc = self.invoke(None)
        self.assertEqual(proc.returncode, 0, proc.stderr.decode())
        self.assertNotIn(b'ALICE_VQE_TAP', proc.stderr)
