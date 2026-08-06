#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
SERVICE_NAME=dbus-shelly-pro-3em-smartmeter
SERVICE_DIR="$SCRIPT_DIR/service"
PERSISTENT_LINK="/opt/victronenergy/service/$SERVICE_NAME"
RUNTIME_LINK="/service/$SERVICE_NAME"
RC_LOCAL=/data/rc.local

if [ "$(readlink "$RUNTIME_LINK" 2>/dev/null || true)" = "$SERVICE_DIR" ]; then
    svc -d "$RUNTIME_LINK" 2>/dev/null || true
fi

ROOT_WAS_RO=$(awk '$2 == "/" && $4 ~ /(^|,)ro(,|$)/ { print 1 }' /proc/mounts)
[ -z "$ROOT_WAS_RO" ] || mount -o remount,rw /
trap '[ -z "$ROOT_WAS_RO" ] || mount -o remount,ro /' 0

for LINK in "$PERSISTENT_LINK" "$RUNTIME_LINK"; do
    [ "$(readlink "$LINK" 2>/dev/null || true)" != "$SERVICE_DIR" ] || rm -f "$LINK"
done

[ ! -f "$RC_LOCAL" ] || sed -i "\|^$SCRIPT_DIR/install.sh$|d" "$RC_LOCAL"
