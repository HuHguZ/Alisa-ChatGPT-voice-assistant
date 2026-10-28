#!/usr/bin/env python3
"""Developer-only build; normal users install the prebuilt ARM artifact."""
import subprocess
from pathlib import Path
root=Path(__file__).resolve().parents[1]
(root/'build').mkdir(exist_ok=True)
subprocess.run([str(root/'engines/native/build/arm/arm-cc'),'-shared','-fPIC','-Os',str(root/'station/mark.c'),
                '-Wl,--no-as-needed','-ldl','-o',str(root/'build/mark.so')],check=True)
