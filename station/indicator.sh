#!/bin/sh
# Only the temporary ChatGPT speaking overlay; no firmware files are edited.
set -eu
LED=${ALICE_LED_ROOT:-/sys/class/leds/plum}
owned=0
saved=''
saved_triggers=''
blue=' 0 0 160 0 0 160 0 0 160'
active_trigger() {
    IFS= read -r task_led_available < "$1/trigger"
    task_led_trigger=${task_led_available#*\[}
    task_led_trigger=${task_led_trigger%%\]*}
    case "$task_led_trigger" in
        ''|*[!a-zA-Z0-9_:-]*) return 1 ;;
    esac
    printf '%s' "$task_led_trigger"
}
triggers() {
    task_led_triggers=''
    for task_led_zone in 1 2 3; do
        for task_led_color in red green blue; do
            task_led_trigger=$(active_trigger "$LED:$task_led_color:ring$task_led_zone")
            task_led_triggers="$task_led_triggers $task_led_trigger"
        done
    done
    printf '%s' "$task_led_triggers"
}
restore_triggers() {
    set -- $saved_triggers
    for task_led_zone in 1 2 3; do
        for task_led_color in red green blue; do
            task_led_path="$LED:$task_led_color:ring$task_led_zone"
            task_led_current_trigger=$(active_trigger "$task_led_path")
            # Writing brightness=0 detaches a Linux LED trigger. Restore it
            # after the last brightness write, but respect a new trigger owner.
            if [ "$task_led_current_trigger" = none ] && [ "$1" != none ]; then
                printf '%s\n' "$1" > "$task_led_path/trigger"
            fi
            shift
        done
    done
}
frame() {
    task_led_frame=''
    for task_led_zone in 1 2 3; do
        for task_led_color in red green blue; do
            IFS= read -r task_led_value < "$LED:$task_led_color:ring$task_led_zone/brightness"
            task_led_frame="$task_led_frame $task_led_value"
        done
    done
    printf '%s' "$task_led_frame"
}
paint() {
    set -- $1
    for task_led_zone in 1 2 3; do
        for task_led_color in red green blue; do
            printf '%s\n' "$1" > "$LED:$task_led_color:ring$task_led_zone/brightness"
            shift
        done
    done
}
release() {
    if [ "$owned" = 1 ]; then
        task_led_current=$(frame)
        [ "$task_led_current" != "$blue" ] || paint "$saved"
        restore_triggers
        owned=0
        printf 'STOCK\n'
    fi
}
trap 'release; exit 0' TERM INT HUP
trap release EXIT
while IFS= read -r -t 2 task_led_command; do
    case "$task_led_command" in
        B)
            task_led_current=$(frame)
            # Preserve an existing red warning/microphone indicator.
            case "$task_led_current" in
                ' 0 0 0 0 0 0 0 0 0'|"$blue") ;;
                *)
                    set -- $task_led_current
                    if [ "$1" -gt 0 ] && [ "$2" = 0 ] && [ "$3" = 0 ]; then
                        release
                        continue
                    fi
                    ;;
            esac
            if [ "$owned" = 0 ]; then
                saved="$task_led_current"
                saved_triggers=$(triggers)
                owned=1
                paint "$blue"
                printf 'BLUE\n'
            elif [ "$task_led_current" != "$blue" ]; then
                saved="$task_led_current"
                paint "$blue"
            fi
            ;;
        0) release ;;
    esac
done
