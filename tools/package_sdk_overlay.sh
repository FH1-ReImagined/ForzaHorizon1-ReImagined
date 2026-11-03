#!/usr/bin/env bash
# Pack the patched ReXGlue SDK pieces (the Xenos GPU plugin, the runtime and the codegen tool that
# tools/build_rexglue_sdk.sh builds) into the archive the launcher unpacks over the pinned SDK
# nightly. The pieces hold SDK code (BSD-3-Clause) and licensed reconstruction/AA
# dependencies, no game code.
#
# Usage: tools/package_sdk_overlay.sh OUT.tar.gz     (OVERLAY: the overlay prefix, default
#        ~/rexglue-sdk-patched-forza/0.10.0.24/linux-amd64)
set -euo pipefail
OUT="$(realpath -m "${1:?usage: package_sdk_overlay.sh OUT.tar.gz}")"
OVERLAY="${OVERLAY:-$HOME/rexglue-sdk-patched-forza/0.10.0.24/linux-amd64}"
for f in bin/rexglue lib/librexgpu-xenos.so lib/librexruntime.so; do
    [ -f "$OVERLAY/$f" ] || { echo "missing $OVERLAY/$f (run tools/build_rexglue_sdk.sh)" >&2; exit 1; }
done
files=(bin/rexglue lib/librexgpu-xenos.so lib/librexruntime.so)
[ -f "$OVERLAY/lib/libopenxr_loader.so.1" ] && files+=(lib/libopenxr_loader.so.1)  # VR (docs/vr.md)
# ReIGlue's CMake helpers (the code model of the game's code), over the nightly's
[ -f "$OVERLAY/lib/cmake/rexglue/rexglue_helpers.cmake" ] && files+=(lib/cmake/rexglue/rexglue_helpers.cmake)
[ -d "$OVERLAY/fh1-licenses" ] && files+=(fh1-licenses)
tar -C "$OVERLAY" -hczf "$OUT" "${files[@]}"
echo "$OUT"
