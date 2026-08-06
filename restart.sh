#!/bin/sh
set -eu

SERVICE=/service/dbus-shelly-pro-3em-smartmeter
[ -d "$SERVICE" ] || { echo "Error: service is not installed." >&2; exit 1; }
exec svc -t "$SERVICE"
