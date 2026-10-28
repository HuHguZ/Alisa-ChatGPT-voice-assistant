#!/bin/sh
# ALISA_VOICE_INSTALLER: owned init entry, no other services are modified.
case "$1" in
start)
    if [ -f /data/opt/alisa-cli/native-autostart ] || [ -f /data/opt/alisa-cli/vpn-autostart ]; then
        /data/opt/alisa-cli/boot.sh launch
    fi
    ;;
stop) /data/opt/alisa-standalone/run.sh stop || true; /data/opt/alisa-cli/vpn.sh stop || true ;;
esac
