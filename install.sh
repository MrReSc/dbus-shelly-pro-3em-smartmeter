#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
SERVICE_NAME=dbus-shelly-pro-3em-smartmeter
SERVICE_DIR="$SCRIPT_DIR/service"
PERSISTENT_LINK="/opt/victronenergy/service/$SERVICE_NAME"
RUNTIME_LINK="/service/$SERVICE_NAME"
RC_LOCAL=/data/rc.local

ROOT_WAS_RO=$(awk '$2 == "/" && $4 ~ /(^|,)ro(,|$)/ { print 1 }' /proc/mounts)
[ -z "$ROOT_WAS_RO" ] || mount -o remount,rw /
trap '[ -z "$ROOT_WAS_RO" ] || mount -o remount,ro /' 0

# Archives can be extracted with overly permissive directory and configuration
# modes. Apply safe permissions on every install while retaining normal read
# access to the driver sources.
find "$SCRIPT_DIR" -type d -exec chmod 755 '{}' '+'
chmod 755 "$SCRIPT_DIR"/*.sh "$SERVICE_DIR/run" "$SERVICE_DIR/log/run"
chmod 600 "$SCRIPT_DIR/config.ini"

for LINK in "$PERSISTENT_LINK" "$RUNTIME_LINK"; do
    if [ -e "$LINK" ] || [ -L "$LINK" ]; then
        if [ ! -L "$LINK" ] || [ "$(readlink "$LINK")" != "$SERVICE_DIR" ]; then
            echo "Error: $LINK already exists and belongs to another service." >&2
            exit 1
        fi
    fi
done
[ -L "$PERSISTENT_LINK" ] || ln -s "$SERVICE_DIR" "$PERSISTENT_LINK"
[ -L "$RUNTIME_LINK" ] || ln -s "$SERVICE_DIR" "$RUNTIME_LINK"

if [ ! -f "$RC_LOCAL" ]; then
    printf '#!/bin/sh\n\n' > "$RC_LOCAL"
fi
chmod 755 "$RC_LOCAL"
grep -qxF "$SCRIPT_DIR/install.sh" "$RC_LOCAL" ||
    printf '%s\n' "$SCRIPT_DIR/install.sh" >> "$RC_LOCAL"

rm -f "$SCRIPT_DIR/current.log"

# An uninstall leaves an already running supervisor in the "down" state until
# it has exited. Bring that supervisor back up explicitly. If the service was
# already running, restart it so an updated driver is loaded.
if svstat "$RUNTIME_LINK" 2>/dev/null | grep -q ': up '; then
    svc -t "$RUNTIME_LINK" 2>/dev/null || true
else
    svc -u "$RUNTIME_LINK" 2>/dev/null || true
fi
