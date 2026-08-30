#!/usr/bin/env bash
# Launch the Nomad desktop launcher. On NixOS, Qt needs the system C++/GL
# libraries that pip's PySide6 cannot see by default; this script locates and
# exposes them.
set -euo pipefail

cd "$(dirname "$0")/.."

find_nix_lib() {
    find /nix/store -maxdepth 4 -name "$1" -path "*/lib/*" 2>/dev/null | head -1 || true
}

EXTRA_LD=""
for lib in \
    "libstdc++.so.6" \
    "libGL.so.1" \
    "libxkbcommon.so.0" \
    "libfontconfig.so.1" \
    "libxcb.so.1" \
    "libX11.so.6" \
    "libdbus-1.so.3" \
    "libfreetype.so.6" \
    "libglib-2.0.so.0" \
    "libz.so.1" \
    "libzstd.so.1"; do
    path=$(find_nix_lib "$lib")
    if [ -n "$path" ]; then
        EXTRA_LD="${EXTRA_LD}:$(dirname "$path")"
    fi
done

export LD_LIBRARY_PATH="${EXTRA_LD#:}${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
exec .venv/bin/python -m launcher.ui "$@"
