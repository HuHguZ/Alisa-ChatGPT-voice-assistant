#!/usr/bin/env python3
"""Build host test helpers and ARM32 helpers without copying firmware libraries."""
import argparse
import os
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--target', choices=['host', 'arm', 'all'], default='all')
    args = parser.parse_args()
    output = ROOT / 'build'
    output.mkdir(exist_ok=True)
    for target in ['host', 'arm']:
        if args.target not in [target, 'all']:
            continue
        compiler = os.environ.get('HOST_CC', 'clang') if target == 'host' else 'clang'
        if not shutil.which(compiler):
            raise SystemExit(f'Missing compiler: {compiler}')
        for name in ['capture_tap', 'vqe_tap', 'playback_worker', 'native_agent', 'tts_gate']:
            destination = output / f'{name}{".host" if target == "host" else ""}.so'
            flags = ['-O2', '-fPIC', '-shared']
            if target == 'arm':
                flags += ['--target=arm-linux-gnueabihf', '-march=armv7-a',
                          '-mfpu=neon', '-mfloat-abi=hard', '-fuse-ld=lld',
                          '-nostdlib', '-Wl,--hash-style=both']
            command = [compiler, *flags, str(ROOT / f'station/{name}.c'),
                       '-o', str(destination)]
            if target == 'host':
                command += ['-ldl']
            subprocess.run(command, check=True)
            print(destination.relative_to(ROOT))


if __name__ == '__main__':
    main()
