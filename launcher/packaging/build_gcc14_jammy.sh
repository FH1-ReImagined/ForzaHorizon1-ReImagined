#!/usr/bin/env bash
# Build GCC 14 for Ubuntu 22.04 from GNU's release tarball: the C++23 standard library (headers,
# libstdc++, libgcc) that release.yml's sdk-linux job compiles the SDK overlay against with the
# pinned clang. Ubuntu 22.04's own g++ is 12, which lacks C++23's library; the ubuntu-toolchain-r PPA
# had g++-14, but Launchpad can be down for hours. This builds the same compiler from GNU's sources,
# on Ubuntu 22.04 (glibc 2.35), so nothing it puts in a binary needs a newer glibc.
#
# Usage:
#   launcher/packaging/build_gcc14_jammy.sh OUT_DIR
#       runs the build in a podman (or docker) container of the pinned Ubuntu 22.04 image and
#       leaves gcc-14.3.0-jammy-x86_64.tar.xz, its .sha256 and the sources in OUT_DIR
#   launcher/packaging/build_gcc14_jammy.sh --in-container OUT_DIR
#       the build itself, as root on Ubuntu 22.04 (ci-toolchain-gcc.yml runs this in its container)
# Environment: GCC_JOBS (parallel jobs, default nproc), GCC_CACHE (a folder for the downloads,
# kept between runs; default OUT_DIR/cache), CONTAINER_ENGINE (podman or docker).
#
# The result unpacks to gcc-14/ and is relocatable: ci_linux_sdk_setup.sh puts it in ~/gcc-14.
# Two builds from the same image and package versions give the same bytes (fixed paths in the
# container, SOURCE_DATE_EPOCH, sorted tar with fixed owners and times, single-threaded xz); the
# Ubuntu packages it is compiled with are listed in gcc-14/share/fh1-buildinfo.txt.
#
# Choices:
# - --disable-bootstrap: the compiler is built once, by Ubuntu 22.04's GCC 11, instead of three
#   times with a stage comparison. The comparison only proves the compiler builds itself the same;
#   Ubuntu's GCC 11 is a tested compiler, and the libraries that end up in programs (libstdc++,
#   libgcc, libatomic) are compiled by the new GCC 14 either way. Bootstrapping triples the time
#   (hours on a 4-core runner) for no change in what the SDK build uses.
# - GMP, MPFR, MPC and isl are built in the tree (contrib/download_prerequisites, which checks
#   their SHA-512 against the GCC tarball's own list) and linked statically into cc1/cc1plus, and
#   the compiler programs link libstdc++ and libgcc statically: the programs need only glibc.
# - --build/--host/--target x86_64-linux-gnu: Ubuntu's own triplet and multiarch library paths, so
#   the compiler finds crt1.o and the system libraries where Ubuntu has them.
# - C and C++ only, no multilib (no 32-bit), no sanitizer runtimes (clang has its own), no NLS.
set -euo pipefail

GCC_VERSION=14.3.0
GCC_SHA256=e0dc77297625631ac8e50fa92fffefe899a4eb702592da5c32ef04e2293aca3a
# The GCC 14.3 release signature: Richard Biener's key (primary key fingerprint below; the release
# is signed by its RSA subkey 7F74F97C103468EE5D750B583AB00996FC26A641), exported from
# https://ftp.gnu.org/gnu/gnu-keyring.gpg into gcc_release_key.asc.
GCC_SIGNER=13975A70E63C361C73AE69EF6EEB81F8981C74C7
# The Ubuntu 22.04 image (docker.io/library/ubuntu:22.04, multi-arch index digest).
IMAGE=docker.io/library/ubuntu@sha256:076e7bf82319619f28dd0aa9c02ab34b055ad37100395b4f49a2ffba04fb5e11
GCC_URLS=(https://ftp.gnu.org/gnu/gcc https://mirrors.kernel.org/gnu/gcc https://ftpmirror.gnu.org/gcc)
PREFIX=/opt/gcc-14
TARBALL=gcc-$GCC_VERSION-jammy-x86_64.tar.xz
# The release date of GCC 14.3.0 (its signature's time): every file in the tarball gets this time.
export SOURCE_DATE_EPOCH=1748000464
HERE="$(cd "$(dirname "$0")" && pwd)"

if [ "${1:-}" != --in-container ]; then
    OUT="$(realpath -m "${1:?usage: build_gcc14_jammy.sh [--in-container] OUT_DIR}")"
    mkdir -p "$OUT"
    engine="${CONTAINER_ENGINE:-$(command -v podman || command -v docker)}"
    [ -n "$engine" ] || { echo "needs podman or docker" >&2; exit 1; }
    cache="${GCC_CACHE:-$OUT/cache}"
    mkdir -p "$cache"
    # The script folder read-only: the container only writes OUT_DIR and the download cache.
    exec "$engine" run --rm --network=host -e GCC_JOBS="${GCC_JOBS:-$(nproc)}" -e GCC_CACHE=/cache \
        -v "$HERE:/scripts:ro,z" -v "$OUT:/out:z" -v "$cache:/cache:z" \
        "$IMAGE" bash /scripts/build_gcc14_jammy.sh --in-container /out
fi

OUT="${2:?usage: build_gcc14_jammy.sh --in-container OUT_DIR}"
mkdir -p "$OUT"
CACHE="${GCC_CACHE:-$OUT/cache}"
mkdir -p "$CACHE"
JOBS="${GCC_JOBS:-$(nproc)}"
. /etc/os-release
[ "${ID:-}" = ubuntu ] && [ "${VERSION_ID:-}" = 22.04 ] ||
    { echo "the build runs on Ubuntu 22.04 (this is ${PRETTY_NAME:-unknown})" >&2; exit 1; }

export DEBIAN_FRONTEND=noninteractive
apt_ok=0
for attempt in 1 2 3; do
    if apt-get update -q && apt-get install -y -q --no-install-recommends \
            build-essential ca-certificates curl gpgv gnupg xz-utils bzip2 file; then
        apt_ok=1; break
    fi
    sleep "$((attempt * 15))"
done
[ "$apt_ok" = 1 ] || { echo "apt-get could not install the build tools" >&2; exit 1; }

fetch() {  # fetch NAME: from the cache, else from the first GNU mirror that has it
    local name="$1" base
    [ -s "$CACHE/$name" ] && return 0
    for base in "${GCC_URLS[@]}"; do
        if curl -fsSL --connect-timeout 20 --retry 3 --retry-connrefused \
                -o "$CACHE/$name.part" "$base/gcc-$GCC_VERSION/$name"; then
            mv "$CACHE/$name.part" "$CACHE/$name"; return 0
        fi
    done
    echo "could not download $name" >&2; return 1
}
src_tar="gcc-$GCC_VERSION.tar.xz"
fetch "$src_tar"
fetch "$src_tar.sig"
# The sha256 pinned above, then GNU's signature by the pinned key: both must hold.
echo "$GCC_SHA256  $CACHE/$src_tar" | sha256sum -c --quiet - ||
    { echo "$src_tar does not match its pinned SHA-256; removing it" >&2; rm -f "$CACHE/$src_tar"; exit 1; }
keyring="$(mktemp -d)"
gpg --dearmor < "$HERE/gcc_release_key.asc" > "$keyring/gcc.gpg"
gpgv --status-fd 3 --keyring "$keyring/gcc.gpg" "$CACHE/$src_tar.sig" "$CACHE/$src_tar" \
    3> "$keyring/status" 2> "$keyring/log" || { cat "$keyring/log" >&2; exit 1; }
# VALIDSIG <signing key> <date> <time> <expiry> <version> <reserved> <pk algo> <hash algo> <class> <primary key>
awk -v want="$GCC_SIGNER" '$1 == "[GNUPG:]" && $2 == "VALIDSIG" && $NF == want {ok = 1} END {exit !ok}' \
    "$keyring/status" || { echo "$src_tar is not signed by $GCC_SIGNER" >&2; cat "$keyring/status" >&2; exit 1; }
echo "$src_tar: SHA-256 and GNU signature by $GCC_SIGNER good"

# Fixed paths, so the compiler's messages and file names are the same in every build.
rm -rf /build && mkdir -p /build/obj /build/dest
tar -xJf "$CACHE/$src_tar" -C /build
SRC=/build/gcc-$GCC_VERSION
# The prerequisites from the cache when they are there; download_prerequisites checks each one
# against contrib/prerequisites.sha512 (part of the signed tarball) before unpacking it.
prereqs="$(cd "$SRC" && sed -n "s/^\(gmp\|mpfr\|mpc\|isl\|gettext\)='\(.*\)'$/\2/p" contrib/download_prerequisites)"
for ar in $prereqs; do
    if [ -s "$CACHE/$ar" ]; then cp "$CACHE/$ar" "$SRC/$ar"; fi
done
(cd "$SRC" && ./contrib/download_prerequisites --sha512)
for ar in $prereqs; do
    if [ ! -s "$CACHE/$ar" ]; then cp "$SRC/$ar" "$CACHE/$ar"; fi
done

cd /build/obj
"$SRC/configure" --prefix="$PREFIX" \
    --build=x86_64-linux-gnu --host=x86_64-linux-gnu --target=x86_64-linux-gnu \
    --enable-languages=c,c++ --disable-multilib --disable-bootstrap \
    --disable-nls --disable-libsanitizer --disable-werror \
    --enable-checking=release --enable-threads=posix --enable-__cxa_atexit --enable-clocale=gnu \
    --enable-default-pie --enable-linker-build-id \
    --with-static-standard-libraries \
    --with-pkgversion="FH1-ReImagined CI build for Ubuntu 22.04" \
    CFLAGS="-O2 -ffile-prefix-map=/build=." CXXFLAGS="-O2 -ffile-prefix-map=/build=." \
    > /build/configure.log 2>&1 || { tail -50 /build/configure.log >&2; tail -50 config.log >&2; exit 1; }
make -j"$JOBS"
make install-strip DESTDIR=/build/dest
G=/build/dest$PREFIX
# libtool archives name the build's paths; programs never need them.
find "$G" -name '*.la' -delete
# Static libraries keep their symbols, not their debug info; -D keeps the archives deterministic.
# Shared libraries lose what linking against them doesn't need (libgcc_s.so is a linker script).
find "$G" -name '*.a' -exec strip --strip-debug -D {} +
is_elf() { [ "$(head -c 4 "$1" | od -An -c | tr -d ' ')" = '177ELF' ]; }
while IFS= read -r -d '' f; do
    if is_elf "$f"; then strip --strip-unneeded "$f"; fi
done < <(find "$G" -type f \( -name '*.so' -o -name '*.so.*' \) -print0)
# The licenses, and what this build was made with.
mkdir -p "$G/share/licenses/gcc"
cp "$SRC"/COPYING* "$G/share/licenses/gcc/"  # GPLv3, LGPL, the GCC Runtime Library Exception
for ar in $prereqs; do
    d="$SRC/${ar%.tar*}"
    for f in "$d"/COPYING* "$d"/LICENSE; do
        [ -f "$f" ] || continue
        mkdir -p "$G/share/licenses/${ar%.tar*}"
        cp "$f" "$G/share/licenses/${ar%.tar*}/"
    done
done
{
    echo "GCC $GCC_VERSION for Ubuntu 22.04 x86_64, built by launcher/packaging/build_gcc14_jammy.sh"
    echo "source: $src_tar sha256 $GCC_SHA256, signed by $GCC_SIGNER"
    echo "prerequisites (contrib/download_prerequisites, SHA-512 checked):"
    for ar in $prereqs; do echo "  $ar $(sha256sum "$SRC/$ar" | cut -d' ' -f1)"; done
    echo "image: $IMAGE"
    echo "configure: $("$G/bin/gcc" -v 2>&1 | sed -n 's/^Configured with: //p')"
    echo "built with:"
    dpkg-query -W -f='  ${Package} ${Version}\n' gcc-11 g++-11 binutils libc6-dev linux-libc-dev make
} > "$G/share/fh1-buildinfo.txt"

# The tarball: sorted, fixed owners and times, single-threaded xz (its output depends on threads).
tar --sort=name --mtime="@$SOURCE_DATE_EPOCH" --owner=0 --group=0 --numeric-owner \
    --format=gnu -C "$(dirname "$G")" -cf - "$(basename "$G")" | xz -T1 -6 > "$OUT/$TARBALL.part"
mv "$OUT/$TARBALL.part" "$OUT/$TARBALL"
(cd "$OUT" && sha256sum "$TARBALL" > "$TARBALL.sha256")
# The corresponding source, for the release next to the binary (GPLv3 section 6).
mkdir -p "$OUT/source"
cp "$CACHE/$src_tar" "$CACHE/$src_tar.sig" "$OUT/source/"
for ar in $prereqs; do cp "$CACHE/$ar" "$OUT/source/"; done
cat "$OUT/$TARBALL.sha256"
