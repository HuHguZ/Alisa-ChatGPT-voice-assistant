#!/bin/sh
set -eu
BASE=/data/opt/alisa-standalone
if [ -r "$BASE/runtime.env" ]; then . "$BASE/runtime.env"; fi
exec "$BASE/bin/alisa-standalone" "$@"
