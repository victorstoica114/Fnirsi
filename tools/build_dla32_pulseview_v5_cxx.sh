#!/usr/bin/env bash
# Isolated SDK0 V5/C++/PulseView build. No device scan or GUI launch.
set -euo pipefail
project=/tmp/dla32-project
source_root="$project/artifacts/pulseview-dla32-v5-cxx-source"
build=/tmp/dla32-pulseview-v5-cxx-build
prefix=/tmp/dla32-pulseview-v5-cxx-prefix
component=${1:-all}
[[ "$component" =~ ^(all|prepare|sigrok|pulseview|package)$ ]]
export PATH="$prefix/bin:/ucrt64/bin:/usr/bin"
export PKG_CONFIG_PATH="$prefix/lib/pkgconfig:/opt/dla32/lib/pkgconfig:/ucrt64/lib/pkgconfig"
export LC_ALL=C
export CFLAGS='-O2 -g'
export CXXFLAGS='-O2 -g'
# Omitted upload macro is the actual SDK0 default. No queue-policy changes.
export CPPFLAGS='-DDLA_PREARM_EMPTY_READS=1'
if [[ "$component" == all || "$component" == prepare ]]; then
    python "$project/tools/package_dla32_pulseview_v5_cxx.py" prepare "$prefix" "$build"
fi
if [[ "$component" == all || "$component" == sigrok ]]; then
    mkdir -p "$build/libsigrok" "$prefix" "$build/validation"
    cd "$build/libsigrok"
    "$source_root/libsigrok/configure" --prefix="$prefix" --disable-all-drivers \
        --enable-demo --enable-fnirsi-dla32 --enable-cxx \
        --disable-python --disable-ruby --disable-java
    make -j4
    make check
    make install
    # Reuse frozen actual-driver/transport fixtures, writing every EXE/log here.
    for fixture in sdk0 timeout; do
        extra=()
        [[ "$fixture" != timeout ]] || extra=(-Werror -Wno-unused-function)
        gcc -std=c99 -O1 -Wall -Wextra -ffunction-sections -fdata-sections \
            -DDLA_PREARM_EMPTY_READS=1 "${extra[@]}" \
            -I"$build/libsigrok" -I"$build/libsigrok/include" \
            -I"$source_root/libsigrok/include" -I"$source_root/libsigrok/src" \
            $(pkg-config --cflags libsigrok) \
            "$project/artifacts/dla32-wch-stop-v5-tests/test_dla32_wch_stop_v5_${fixture}_unit.c" \
            "$build/libsigrok/.libs/libsigrok.a" $(pkg-config --libs --static libsigrok) \
            -Wl,--gc-sections -o "$build/validation/$fixture.exe"
        "$build/validation/$fixture.exe" > "$build/validation/$fixture.log" 2>&1
        tail -n 1 "$build/validation/$fixture.log"
    done
    cat > "$build/validation/cxx-version.cpp" <<'CPP'
#include <libsigrokcxx/libsigrokcxx.hpp>
#include <windows.h>
#include <iostream>
int main() {
    // Static version getters do not initialize drivers, scan or call the SDK.
    std::cout << "libsigrokcxx package=" << sigrok::Context::package_version()
              << " ABI=" << sigrok::Context::lib_version() << '\n';
    for (const wchar_t *name : {L"libsigrok-4.dll", L"libsigrokcxx-4.dll"}) {
        wchar_t path[32768];
        HMODULE module = GetModuleHandleW(name);
        DWORD size = module ? GetModuleFileNameW(module, path, 32768) : 0;
        if (!size || size >= 32768) return 2;
        std::wcout << name << L"=" << path << L'\n';
    }
    return 0;
}
CPP
    g++ -std=c++17 -O2 -Wall -Wextra -Werror $(pkg-config --cflags libsigrokcxx) \
        "$build/validation/cxx-version.cpp" $(pkg-config --libs libsigrokcxx) \
        -o "$build/validation/cxx-version.exe"
fi
if [[ "$component" == all || "$component" == pulseview ]]; then
    cmake -S "$source_root/pulseview" -B "$build/pulseview" -G Ninja \
        -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX="$prefix" \
        -DCMAKE_PREFIX_PATH="$prefix;/opt/dla32;/ucrt64" \
        -DSTATIC_PKGDEPS_LIBS=OFF -DCMAKE_POLICY_VERSION_MINIMUM=3.5 \
        -DENABLE_TESTS=OFF -DENABLE_TS_UPDATE=OFF
    cmake --build "$build/pulseview" -j4
    cmake --install "$build/pulseview"
fi
if [[ "$component" == all || "$component" == package ]]; then
    python "$project/tools/package_dla32_pulseview_v5_cxx.py" package "$prefix" "$build"
fi
printf 'Isolated C++/PulseView V5 stage complete: %s (no hardware)\n' "$component"
