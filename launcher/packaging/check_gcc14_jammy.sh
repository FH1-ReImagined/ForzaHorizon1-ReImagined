#!/usr/bin/env bash
# Check a GCC 14 tarball from build_gcc14_jammy.sh on Ubuntu 22.04 (ci-toolchain-gcc.yml runs it
# after the build; run it by hand in an ubuntu:22.04 container):
#   - every ELF file in it needs glibc 2.35 or older, and the compiler programs need only glibc
#   - the C++23 headers are there
#   - a C++23 program (std::expected, std::print, ranges, std::generator) compiles, links and runs
#     with its own g++ and, when CLANG++ is given, with that clang and --gcc-install-dir, the way
#     ci_linux_sdk_setup.sh sets clang up; what the programs need is glibc 2.35 or older too
# Usage: check_gcc14_jammy.sh TARBALL [CLANG++]
set -euo pipefail
TARBALL="$(realpath "${1:?usage: check_gcc14_jammy.sh TARBALL [CLANG++]}")"
CLANGXX="${2:-}"
MAX_GLIBC=2.35
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
tar -xJf "$TARBALL" -C "$work"
G="$work/gcc-14"
[ -x "$G/bin/g++" ] || { echo "no gcc-14/bin/g++ in $TARBALL" >&2; exit 1; }
fail=0

# The newest GLIBC_ version an ELF file asks for (its dynamic symbols' versions).
newest_glibc() { { objdump -T "$1" 2>/dev/null || true; } | grep -o 'GLIBC_[0-9.]*[0-9]' | sort -uV | tail -1 || true; }
newest() { { objdump -T "$2" 2>/dev/null || true; } | grep -o "$1[0-9.]*[0-9]" | sort -uV | tail -1 || true; }
too_new() { [ -n "$1" ] && [ "$(printf '%s\n%s\n' "${1#GLIBC_}" "$MAX_GLIBC" | sort -V | tail -1)" != "$MAX_GLIBC" ]; }
# Programs and shared libraries (ELF types 2 and 3); objects (crtbegin.o and the like) have no
# versioned references until they are linked.
elves=0
while IFS= read -r -d '' f; do
    [ "$(head -c 4 "$f" | od -An -c | tr -d ' ')" = '177ELF' ] || continue
    case "$(od -An -t u2 -j 16 -N 2 "$f" | tr -d ' ')" in 2|3) ;; *) continue ;; esac
    elves=$((elves + 1))
    v="$(newest_glibc "$f")"
    if too_new "$v"; then echo "FAIL ${f#"$work/"} needs $v" >&2; fail=1; fi
done < <(find "$G" -type f -print0)
echo "ELF programs and libraries: $elves, none needs a glibc newer than $MAX_GLIBC: $([ "$fail" = 0 ] && echo yes || echo NO)"
# The compiler programs: glibc only (GMP, MPFR, MPC, isl, zlib and libstdc++ are linked in).
for f in "$G"/bin/g++ "$G"/bin/gcc "$G"/libexec/gcc/x86_64-linux-gnu/14.3.0/cc1plus \
         "$G"/libexec/gcc/x86_64-linux-gnu/14.3.0/cc1 "$G"/libexec/gcc/x86_64-linux-gnu/14.3.0/lto1; do
    needed="$(objdump -p "$f" | awk '$1 == "NEEDED" {print $2}' | sort | tr '\n' ' ')"
    extra="$(printf '%s\n' $needed | grep -vxE 'libc\.so\.6|libm\.so\.6|ld-linux-x86-64\.so\.2|libdl\.so\.2|libpthread\.so\.0' || true)"
    echo "${f#"$G/"}: $needed"
    if [ -n "$extra" ]; then echo "FAIL ${f#"$G/"} needs $extra" >&2; fail=1; fi
done
libstdcxx="$(readlink -f "$G/lib64/libstdc++.so.6")"
echo "libstdc++: ${libstdcxx#"$G/"}, newest $(newest GLIBCXX_ "$libstdcxx") $(newest CXXABI_ "$libstdcxx"), needs $(newest_glibc "$libstdcxx")"

inc="$G/include/c++/14.3.0"
for h in expected print ranges generator stacktrace spanstream mdspan flat_map; do
    if [ -f "$inc/$h" ]; then printf 'header <%s>: yes\n' "$h"
    elif [ "$h" = mdspan ] || [ "$h" = flat_map ]; then printf 'header <%s>: no (GCC 15)\n' "$h"
    else printf 'FAIL header <%s> missing\n' "$h" >&2; fail=1; fi
done

cat > "$work/t.cpp" <<'EOF'
#include <algorithm>
#include <expected>
#include <generator>
#include <print>
#include <ranges>
#include <string>
#include <string_view>
#include <vector>

static std::expected<int, std::string> parse(std::string_view s) {
    int v = 0;
    for (char c : s) {
        if (c < '0' || c > '9') return std::unexpected("not a number: " + std::string(s));
        v = v * 10 + (c - '0');
    }
    return v;
}

static std::generator<int> squares(int n) {
    for (int i = 1; i <= n; ++i) co_yield i * i;
}

int main() {
    auto v = std::views::iota(1, 6) | std::views::transform([](int x) { return x * 3; })
             | std::ranges::to<std::vector>();
    int sum = 0;
    for (auto [i, x] : std::views::enumerate(v)) sum += static_cast<int>(i) * x;
    for (int s : squares(4)) sum += s;
    auto ok = parse("42").and_then([](int x) -> std::expected<int, std::string> { return x + 1; });
    auto bad = parse("4x2");
    if (!ok || *ok != 43 || bad || std::ranges::fold_left(v, 0, std::plus{}) != 45) return 1;
    std::println("C++23 ok: {} {} {}", sum, *ok, bad.error());
    return sum == 150 ? 0 : 2;
}
EOF
run_test() {  # run_test LABEL COMPILER ARGS...
    local label="$1"; shift
    if ! "$@" -std=c++23 -O2 -Wall -Wextra -Werror "$work/t.cpp" -o "$work/t-$label"; then
        echo "FAIL $label: the C++23 program does not compile" >&2; fail=1; return
    fi
    if ! LD_LIBRARY_PATH="$G/lib64" "$work/t-$label"; then
        echo "FAIL $label: the C++23 program did not run" >&2; fail=1; return
    fi
    local v; v="$(newest_glibc "$work/t-$label")"
    if too_new "$v"; then echo "FAIL $label: the program needs $v" >&2; fail=1; fi
    echo "$label: built and ran; needs $v, $(newest GLIBCXX_ "$work/t-$label")"
}
run_test g++ "$G/bin/g++"
if [ -n "$CLANGXX" ]; then
    flags=(--gcc-install-dir="$G/lib/gcc/x86_64-linux-gnu/14.3.0")
    # The include and library paths must be this GCC's, not another one on the system.
    "$CLANGXX" "${flags[@]}" -std=c++23 -v -fsyntax-only "$work/t.cpp" 2> "$work/clang-v" || true
    search="$(sed -n 's/^ \(\/.*\)/\1/p' "$work/clang-v" | xargs -r -n1 realpath -m)"
    if ! grep -qxF "$inc" <<< "$search"; then
        echo "FAIL clang does not search $inc:" >&2; echo "$search" >&2; fail=1
    fi
    if grep -q '^/usr/include/c++/' <<< "$search"; then
        echo "FAIL clang also searches the system's C++ headers:" >&2; echo "$search" >&2; fail=1
    fi
    "$CLANGXX" "${flags[@]}" -### "$work/t.cpp" -o "$work/x" 2> "$work/clang-link" || true
    grep -q -- "-L$G/lib/gcc/x86_64-linux-gnu/14.3.0/../../../../lib64" "$work/clang-link" ||
        { echo "FAIL clang does not link with $G/lib64" >&2; fail=1; }
    run_test clang++ "$CLANGXX" "${flags[@]}"
fi
[ "$fail" = 0 ] && echo "all checks passed" || { echo "checks FAILED" >&2; exit 1; }
