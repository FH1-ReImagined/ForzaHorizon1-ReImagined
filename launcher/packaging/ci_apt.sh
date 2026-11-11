#!/usr/bin/env bash
# CI helper (release.yml): apt-get with retries on GitHub's Ubuntu runners. Takes apt-get's arguments:
#   launcher/packaging/ci_apt.sh update
#   launcher/packaging/ci_apt.sh install -y libegl1 libdbus-1-3
# The runners download from azure.archive.ubuntu.com first (through /etc/apt/apt-mirrors.txt). When
# that name stops resolving, APT does not fall through to the list's other mirrors, so the Azure
# mirror is swapped for archive.ubuntu.com: the same archive under the same signing key.
# security.ubuntu.com entries stay as they are.
set -euo pipefail
SUDO=""; [ "$(id -u)" = 0 ] || SUDO=sudo
export DEBIAN_FRONTEND=noninteractive
APT_ETC="${CI_APT_ETC:-/etc/apt}"
AZURE=azure.archive.ubuntu.com
log="$(mktemp)"
trap 'rm -f "$log"' EXIT

azure_files() {
    grep -lsF "$AZURE" "$APT_ETC/apt-mirrors.txt" "$APT_ETC/sources.list" "$APT_ETC"/sources.list.d/* || true
}
switched=0
use_main_archive() {
    local files
    files="$(azure_files)"
    [ "$switched" = 0 ] && [ -n "$files" ] || return 1
    switched=1
    echo "ci_apt: $AZURE is unreachable; using archive.ubuntu.com" >&2
    # shellcheck disable=SC2086 # one file per word; APT's file names have no spaces
    $SUDO sed -i "s#${AZURE//./\\.}#archive.ubuntu.com#g" $files
    # Index files named after the old host no longer count, so a later install needs an update.
    [ "$1" = update ] || $SUDO apt-get -o Acquire::Retries=3 update || true
}

# A failed lookup costs APT about 20 seconds per try and file, so check the name once up front.
if [ -n "$(azure_files)" ] && ! getent hosts "$AZURE" >/dev/null; then
    use_main_archive "${1:-}" || true
fi
attempt=1
while :; do
    status=0
    $SUDO apt-get -o Acquire::Retries=3 "$@" 2>&1 | tee "$log" || status=$?
    # "update" only warns when an index fails, so look for the Azure mirror in the output too.
    if grep -iE "(fail|could not|unable).*${AZURE//./\\.}" "$log" >/dev/null && use_main_archive "${1:-}"; then
        continue
    fi
    [ "$status" -ne 0 ] || exit 0
    [ "$attempt" -lt 3 ] || exit "$status"
    echo "APT failed (attempt $attempt/3); retrying in $((attempt * 10)) seconds" >&2
    sleep "$((attempt * 10))"
    attempt=$((attempt + 1))
done
