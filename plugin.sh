#!/bin/sh
here="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
sys="$(uname -s)"
case "$sys" in
MINGW* | MSYS* | CYGWIN*) rel="x86_64-pc-windows-msvc/gray-hands.exe" ;;
Darwin*)
    case "$(uname -m)" in
    arm64) rel="aarch64-apple-darwin/gray-hands" ;;
    *) rel="x86_64-apple-darwin/gray-hands" ;;
    esac ;;
*)
    case "$(uname -m)" in
    aarch64 | arm64) rel="aarch64-unknown-linux-musl/gray-hands" ;;
    *) rel="x86_64-unknown-linux-musl/gray-hands" ;;
    esac ;;
esac
if [ -x "$here/$rel" ]; then exec "$here/$rel" "$@"; fi
for cand in "$here/target/release/gray-hands.exe" "$here/target/release/gray-hands" "$here/target/debug/gray-hands.exe" "$here/target/debug/gray-hands"; do
    if [ -x "$cand" ]; then exec "$cand" "$@"; fi
done
echo "gray-hands binary not found in $here" >&2
exit 1
