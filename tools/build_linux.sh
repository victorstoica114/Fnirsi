#!/usr/bin/env bash
# Build libsigrok (with the FNIRSI DLA-32 driver), libsigrokdecode,
# sigrok-cli and PulseView on Linux
# into a local prefix. Usage: tools/build_linux.sh [PREFIX] [BUILD_DIR]
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd)
PREFIX=${1:-$ROOT/../prefix}
BUILD=${2:-$ROOT/../build}
JOBS=$(nproc)
mkdir -p "$PREFIX" "$BUILD"
PREFIX=$(cd "$PREFIX" && pwd)
export PKG_CONFIG_PATH="$PREFIX/lib/pkgconfig${PKG_CONFIG_PATH:+:$PKG_CONFIG_PATH}"

build() { # name, extra configure args...
	local name=$1; shift
	rsync -a --delete "$ROOT/src/$name/" "$BUILD/$name/"
	(cd "$BUILD/$name" && ./autogen.sh && ./configure --prefix="$PREFIX" "$@" \
		&& make -j"$JOBS" && make install)
}

build libsigrok --enable-cxx --disable-java --disable-ruby
build libsigrokdecode
build sigrok-cli

rsync -a --delete "$ROOT/src/pulseview/" "$BUILD/pulseview/"
cmake -S "$BUILD/pulseview" -B "$BUILD/pulseview/build" \
	-DCMAKE_INSTALL_PREFIX="$PREFIX" -DCMAKE_BUILD_TYPE=Release \
	-DDISABLE_WERROR=TRUE -DENABLE_TESTS=FALSE
cmake --build "$BUILD/pulseview/build" -j"$JOBS"
cmake --install "$BUILD/pulseview/build"
echo "Built into $PREFIX. Run with:"
echo "  LD_LIBRARY_PATH=$PREFIX/lib $PREFIX/bin/sigrok-cli -d fnirsi-dla32:conn=<bus>.<addr> --show"
echo "  LD_LIBRARY_PATH=$PREFIX/lib $PREFIX/bin/pulseview -d fnirsi-dla32:conn=<bus>.<addr>"
