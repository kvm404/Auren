#!/bin/bash
# Build libptts_ffi.so, Phonon's CPU text-to-speech library (gradium-ai/xn-ptts), for the app's
# arm64-v8a phones, into app/src/main/jniLibs/arm64-v8a/. The library is not committed: this script
# builds it from a pinned release, so the same source always gives the app the same library.
#
# Needs: rustup/cargo, the Android SDK (ANDROID_HOME, or ~/Android/Sdk) with cmdline-tools, and a JDK
# for sdkmanager (JAVA_HOME). It installs what else it needs, each pinned: the NDK (with sdkmanager),
# cargo-ndk, and the aarch64-linux-android Rust target. The xn-ptts checkout lives outside the repo,
# in $AUREN_BUILD_DIR (default ~/.cache/auren-build).
#
# Without the library the app still builds and runs: it reads replies with Android's own voice.
set -euo pipefail

PTTS_TAG=v0.7.1
PTTS_COMMIT=5f9522b3e245846d312caaeb460d3dcae5d1a991
NDK_VERSION=29.0.14206865        # the NDK xn-ptts's own Android build (qnn/android) pins; r28+ aligns to 16 KB pages
CARGO_NDK_VERSION=4.1.2

here="$(cd "$(dirname "$0")" && pwd)"
out="$here/../app/src/main/jniLibs"
build="${AUREN_BUILD_DIR:-$HOME/.cache/auren-build}"
src="$build/xn-ptts"
sdk="${ANDROID_HOME:-${ANDROID_SDK_ROOT:-$HOME/Android/Sdk}}"
export PATH="$HOME/.cargo/bin:$PATH"

# The source, at the pinned commit (a moved tag is refused).
mkdir -p "$build"
[ -d "$src/.git" ] || git clone --quiet https://github.com/gradium-ai/xn-ptts.git "$src"
git -C "$src" fetch --quiet --tags origin
git -C "$src" -c advice.detachedHead=false checkout --quiet "$PTTS_TAG"
got="$(git -C "$src" rev-parse HEAD)"
[ "$got" = "$PTTS_COMMIT" ] || { echo "xn-ptts $PTTS_TAG is $got, expected $PTTS_COMMIT" >&2; exit 1; }

# The NDK.
ndk="$sdk/ndk/$NDK_VERSION"
if [ ! -d "$ndk" ]; then
    yes | "$sdk/cmdline-tools/latest/bin/sdkmanager" --install "ndk;$NDK_VERSION" >/dev/null
fi
export ANDROID_NDK_HOME="$ndk"

# cargo-ndk and the Rust target.
if [ "$(cargo ndk --version 2>/dev/null | awk '{print $2}')" != "$CARGO_NDK_VERSION" ]; then
    cargo install --quiet --locked cargo-ndk --version "$CARGO_NDK_VERSION"
fi
rustup target add aarch64-linux-android >/dev/null

# The flags xn-ptts's android/build.sh uses, for arm64 only (the phones this app targets; no
# emulator build). xn picks its kernels at compile time: dotprod and fp16 (ARMv8.2) are on nearly
# every phone since 2019, and the app checks the phone has them before it loads the library (a
# phone without them would crash in it). They replace .cargo/config.toml's target-cpu=native.
# Stripping keeps the exported ptts_* calls; 16 KB pages are what Android 15+ wants.
export CARGO_TARGET_AARCH64_LINUX_ANDROID_RUSTFLAGS="-C strip=symbols -C link-arg=-Wl,-z,max-page-size=16384 -C target-cpu=generic -C target-feature=+dotprod,+fp16"
unset RUSTFLAGS CARGO_ENCODED_RUSTFLAGS

# cargo-ndk skips the copy when the file already there is newer, so start from an empty folder.
rm -rf "$out/arm64-v8a"
(cd "$src" && cargo ndk -t arm64-v8a -o "$out" build --release --locked -p ptts-ffi)
ls -l "$out/arm64-v8a/libptts_ffi.so"
sha256sum "$out/arm64-v8a/libptts_ffi.so"
