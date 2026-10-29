#!/bin/sh
set -eu
umask 077
mkdir -p "${DUET_DATA_DIR:-/app/data}"
exec "$@"
