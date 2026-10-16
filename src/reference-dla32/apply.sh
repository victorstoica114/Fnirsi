#!/bin/sh
# AI-generated with assistance from GitHub Copilot.
# Model: GitHub Copilot; the underlying model identifier was not exposed.
# Human review and hardware validation were performed for this integration.
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PXVIEW_ROOT=${1:-"$SCRIPT_DIR/../PXView"}
PXVIEW_ROOT=$(CDPATH= cd -- "$PXVIEW_ROOT" && pwd)
LIBSIGROK_ROOT="$PXVIEW_ROOT/libsigrok"

expected_pxview=59263ff70602e40dc2f3e659b0b434aab30dd500
expected_libsigrok=34362ac9120dbe124e74b1a8c0efd6611b9ecff1

actual_pxview=$(git -C "$PXVIEW_ROOT" rev-parse HEAD)
actual_libsigrok=$(git -C "$LIBSIGROK_ROOT" rev-parse HEAD)

if [ "$actual_pxview" != "$expected_pxview" ]; then
    echo "PXView revision mismatch: expected $expected_pxview, got $actual_pxview" >&2
    exit 1
fi
if [ "$actual_libsigrok" != "$expected_libsigrok" ]; then
    echo "libsigrok revision mismatch: expected $expected_libsigrok, got $actual_libsigrok" >&2
    exit 1
fi

git -C "$PXVIEW_ROOT" apply --check "$SCRIPT_DIR/pxview-build.patch"
git -C "$LIBSIGROK_ROOT" apply --check "$SCRIPT_DIR/fnirsi-protocol.patch"
git -C "$PXVIEW_ROOT" apply "$SCRIPT_DIR/pxview-build.patch"
git -C "$LIBSIGROK_ROOT" apply "$SCRIPT_DIR/fnirsi-protocol.patch"

echo "Applied PXView DLA32 overlay to $PXVIEW_ROOT"
