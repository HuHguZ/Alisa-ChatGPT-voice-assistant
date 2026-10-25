#!/bin/sh
# Only the temporary ChatGPT speaking overlay; no firmware files are edited.
set -eu
LED=${ALICE_LED_ROOT:-/sys/class/leds/plum}
owned=0
saved=''
blue=' 0 0 160 0 0 160 0 0 160'
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
                        owned=0
                        continue
                    fi
                    ;;
            esac
            if [ "$owned" = 0 ]; then
                saved="$task_led_current"
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
