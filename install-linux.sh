#!/bin/sh
set -eu
command -v python3 >/dev/null || { echo 'Установите Python 3.10 или новее'; exit 1; }
ALISA_SOURCE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
exec python3 "$ALISA_SOURCE/scripts/release.py" --install
