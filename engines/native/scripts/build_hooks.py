#!/usr/bin/env python3
"""Build pinned-ABI preloads without bundling another C/C++ runtime."""
from pathlib import Path
import subprocess

ROOT=Path(__file__).resolve().parents[1]
for name in ['native_agent','vqe_tap','tts_gate']:
    subprocess.run(['clang','--target=arm-linux-gnueabihf','-mcpu=cortex-a53',
        '-fuse-ld=lld','-fPIC','-shared','-nostdlib','-O2','-fno-builtin',
        '-Wl,--hash-style=both','-Wl,-s','-Wall','-Wextra',
        str(ROOT/'station'/(name+'.c')),'-o',str(ROOT/'build'/(name+'.so'))],check=True)
    symbols=subprocess.check_output(['readelf','-Ws',str(ROOT/'build'/(name+'.so'))],text=True)
    if any(' UND ' in line and '__aeabi_' in line for line in symbols.splitlines()):
        raise RuntimeError('Preload requires an unavailable ARM compiler runtime')
print('Three ARMhf preloads built.')
