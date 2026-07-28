#!/usr/bin/env bash
# alx-whisper launcher
# Used by systemd --user unit. For manual use: ./alx-whisper.sh start|stop|status|logs

set -euo pipefail

NAME="alx-whisper"
DATA_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/$NAME"
CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/$NAME"
PID_FILE="$DATA_DIR/$NAME.pid"
LOG_FILE="$DATA_DIR/$NAME.log"
DAEMON="$DATA_DIR/alx-whisper-daemon.py"

mkdir -p "$DATA_DIR"

# Load env
if [[ -f "$CONFIG_DIR/.env" ]]; then
    set -a
    # shellcheck disable=SC1090,SC1091
    source "$CONFIG_DIR/.env"
    set +a
else
    echo "[$NAME] ERROR: $CONFIG_DIR/.env not found" >&2
    exit 1
fi

# Pick python: prefer venv if present, else system
PYTHON_BIN="$DATA_DIR/.venv/bin/python3"
if [[ ! -x "$PYTHON_BIN" ]]; then
    PYTHON_BIN="$(command -v python3)"
fi

case "${1:-start}" in
    start)
        if [[ -f "$PID_FILE" ]] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
            echo "[$NAME] already running (pid $(cat "$PID_FILE"))"
            exit 0
        fi
        echo "[$NAME] starting..."
        nohup "$PYTHON_BIN" "$DAEMON" >>"$LOG_FILE" 2>&1 &
        echo $! > "$PID_FILE"
        sleep 0.5
        if kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
            echo "[$NAME] started (pid $(cat "$PID_FILE"))"
        else
            echo "[$NAME] failed to start, see $LOG_FILE" >&2
            exit 1
        fi
        ;;
    stop)
        if [[ -f "$PID_FILE" ]] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
            kill "$(cat "$PID_FILE")"
            rm -f "$PID_FILE"
            echo "[$NAME] stopped"
        else
            echo "[$NAME] not running"
            rm -f "$PID_FILE"
        fi
        ;;
    status)
        if [[ -f "$PID_FILE" ]] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
            echo "[$NAME] running (pid $(cat "$PID_FILE"))"
        else
            echo "[$NAME] stopped"
            exit 1
        fi
        ;;
    logs)
        tail -n 100 -F "$LOG_FILE"
        ;;
    *)
        echo "Usage: $0 {start|stop|status|logs}" >&2
        exit 2
        ;;
esac
