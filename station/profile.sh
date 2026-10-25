#!/bin/sh
set -eu
BASE=/data/opt/alice-voice
Q=/system/vendor/quasar

cleanup_receivers() {
    # A remote command without a PTY may survive a dropped SSH connection.
    # Match only this prototype's receiver and its earlier capture probes.
    for task_audio_pid in $(pidof arecord socat aplay 2>/dev/null || true); do
        [ -r "/proc/$task_audio_pid/cmdline" ] || continue
        task_audio_command=$(tr '\000' ' ' < "/proc/$task_audio_pid/cmdline")
        case "$task_audio_command" in
            *alice_mic*|*alice_shared_capture*|*"$BASE/microphone.sock"*|*"$BASE/playback.pid"*|*"$BASE/native_agent.so"*)
                kill -KILL "$task_audio_pid" 2>/dev/null || true
                ;;
        esac
    done
    rm -f "$BASE/microphone.sock" "$BASE/native-muted"
}

cancel_guard() {
    rm -f "$BASE/rollback.pending"
    if [ -s "$BASE/rollback.pid" ]; then
        task_guard_pid=$(cat "$BASE/rollback.pid")
        if [ -r "/proc/$task_guard_pid/cmdline" ]; then
            task_guard_command=$(tr '\000' ' ' < "/proc/$task_guard_pid/cmdline")
            case "$task_guard_command" in
                *rollback.pending*|*profile.sh\ guard*) kill "$task_guard_pid" 2>/dev/null || true ;;
            esac
        fi
        rm -f "$BASE/rollback.pid"
    fi
}

case "${1:-}" in
    guard)
        sleep 180
        if [ -f "$BASE/rollback.pending" ]; then
            cleanup_receivers
            unset ALSA_CONFIG_PATH
            "$Q/q" restart
            rm -f "$BASE/rollback.pending" "$BASE/rollback.pid"
        fi
        ;;
    bridge)
        cancel_guard
        cleanup_receivers
        touch "$BASE/rollback.pending"
        nohup "$BASE/profile.sh" guard >"$BASE/rollback.log" 2>&1 </dev/null &
        echo $! >"$BASE/rollback.pid"
        # The stock stop command exits nonzero when an optional process is absent.
        "$Q/q" stop || true
        if pidof quasar_launcher2 >/dev/null; then
            echo 'The stock launcher is still running' >&2
            exit 1
        fi
        cd /data/quasar
        export CA_CERTIFICATES="$Q/ca-certificates.crt"
        export LD_LIBRARY_PATH="$Q/libs"
        ulimit -n 10240
        ulimit -s 1024
        nohup "$Q/quasar_launcher2" "$BASE/launch.bridge.json" >"$BASE/launcher.log" 2>&1 </dev/null &
        sleep 1
        ;;
    commit)
        cancel_guard
        ;;
    stock)
        cancel_guard
        cleanup_receivers
        unset ALSA_CONFIG_PATH
        "$Q/q" restart
        ;;
    *)
        echo 'Usage: profile.sh bridge|commit|stock' >&2
        exit 2
        ;;
esac
