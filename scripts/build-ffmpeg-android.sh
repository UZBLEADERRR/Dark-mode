#!/usr/bin/env bash
#
# Build the ffmpeg and ffprobe the phone runs.
#
# The app shells out to ffmpeg for every frame it draws, so the phone build needs
# one — and there is no package manager on Android to install it from. This
# cross-compiles a static pair against the NDK, which is the whole of the phone
# build that cannot be expressed in Gradle.
#
# Two constraints shape it:
#
# **Static.** Each binary is linked against its own copy of x264, freetype and
# libass, because Android has no place to put a shared library that another
# process would find, and `lib*.so` in the package's library folder is the only
# file the kernel will let this app execute at all.
#
# **libass without fontconfig.** There is no font database on Android to ask, so
# libass is built with only its directory provider and the app names a folder in
# `SUBTITLE_FONTSDIR`. That is the only piece of libass that is optional:
# fribidi and harfbuzz are hard requirements of 0.17 with no flag to turn them
# off, which is why both are built here — and why harfbuzz is pinned to 2.9.1,
# the last release that still ships an autotools `configure` and so needs no
# second build system for one library.
#
# Usage:  ANDROID_NDK_HOME=/path/to/ndk scripts/build-ffmpeg-android.sh out/
# Result: out/libffmpeg.so and out/libffprobe.so, both arm64-v8a executables.

set -euo pipefail

mkdir -p "${1:-out}"
OUT_DIR="$(cd "${1:-out}" && pwd)"
WORK="${WORK_DIR:-$(pwd)/.ffmpeg-build}"

X264_TAG="${X264_TAG:-stable}"
FREETYPE_VERSION="${FREETYPE_VERSION:-2.13.3}"
HARFBUZZ_VERSION="${HARFBUZZ_VERSION:-2.9.1}"
FRIBIDI_VERSION="${FRIBIDI_VERSION:-1.0.16}"
LIBASS_VERSION="${LIBASS_VERSION:-0.17.3}"
FFMPEG_VERSION="${FFMPEG_VERSION:-7.1}"

# 26 is the app's own minSdk. Raising it here without raising it there produces
# binaries that refuse to load on devices the app otherwise installs on.
API="${ANDROID_API:-26}"
ARCH=aarch64
TRIPLE=aarch64-linux-android

: "${ANDROID_NDK_HOME:?set ANDROID_NDK_HOME to the NDK you want to build against}"

HOST_TAG=linux-x86_64
TOOLCHAIN="$ANDROID_NDK_HOME/toolchains/llvm/prebuilt/$HOST_TAG"
[ -d "$TOOLCHAIN" ] || { echo "no toolchain at $TOOLCHAIN" >&2; exit 1; }

PREFIX="$WORK/prefix"
mkdir -p "$WORK" "$PREFIX" "$OUT_DIR"

export PATH="$TOOLCHAIN/bin:$PATH"
export CC="$TOOLCHAIN/bin/${TRIPLE}${API}-clang"
export CXX="$TOOLCHAIN/bin/${TRIPLE}${API}-clang++"
export AR="$TOOLCHAIN/bin/llvm-ar"
export RANLIB="$TOOLCHAIN/bin/llvm-ranlib"
export STRIP="$TOOLCHAIN/bin/llvm-strip"
export NM="$TOOLCHAIN/bin/llvm-nm"
export SYSROOT="$TOOLCHAIN/sysroot"
# `-Wno-error` is not laziness. These are releases from 2021 to 2024 being
# compiled by whatever clang the newest NDK ships, and harfbuzz in particular
# turns its own warnings into errors — so it fails on
# `-Wcast-function-type-strict`, a check that did not exist when it was
# released, over a cast FreeType's own API asks for. A warning invented after
# the code was written is not a reason to refuse to build it. Last on the
# command line, where it overrides the `-Werror` a project adds itself.
export CFLAGS="-O3 -fPIC -DANDROID -I$PREFIX/include -Wno-error"
export CXXFLAGS="$CFLAGS"
export LDFLAGS="-L$PREFIX/lib"
# Only our own prefix, never the build machine's: a host .pc file found here
# links the phone binary against a library that is not on the phone.
export PKG_CONFIG_LIBDIR="$PREFIX/lib/pkgconfig"
export PKG_CONFIG_PATH="$PREFIX/lib/pkgconfig"

JOBS="$(nproc 2>/dev/null || echo 4)"

fetch() {  # fetch <directory-it-unpacks-to> <url> [mirror...]
  local dir="$1"; shift
  if [ -d "$WORK/$dir" ]; then echo "→ have $dir"; return; fi
  local url
  for url in "$@"; do
    echo "→ fetching ${url##*/}"
    # Mirrors, because a project's own download host going down should not be
    # the reason a phone build cannot be made. The first one that answers wins.
    if curl -fsSL --retry 3 --retry-delay 3 --max-time 600 -o "$WORK/$dir.tar" "$url"; then
      local top
      # `awk`, not `head`: `head` closes the pipe on the first line, tar is
      # killed writing to it, and under `pipefail` that failure ends the build
      # with "tar: stdout: write error" and nothing about why. awk reads the
      # listing to the end, so tar always finishes.
      top="$(tar -tf "$WORK/$dir.tar" | awk -F/ 'NR==1 {print $1}')"
      tar -xf "$WORK/$dir.tar" -C "$WORK"
      # A mirror is allowed to name its folder differently — a tag archive from
      # GitHub unpacks to `FFmpeg-n7.1` where the project's own tarball says
      # `ffmpeg-7.1` — so the folder is renamed rather than the build taught
      # about every mirror's habits.
      #
      # Written as `if`, not as `&&`: this script runs under `set -e`, where a
      # test that is merely false ends the whole build.
      if [ "$top" != "$dir" ] && [ -d "$WORK/$top" ]; then
        mv "$WORK/$top" "$WORK/$dir"
      fi
      if [ -d "$WORK/$dir" ]; then return; fi
      echo "  ($url unpacked to '$top', which is not a source tree)" >&2
    fi
  done
  echo "could not fetch $dir from any of: $*" >&2
  return 1
}

# ── x264 ──────────────────────────────────────────────────────────────────────
if [ ! -f "$PREFIX/lib/libx264.a" ]; then
  echo "══ x264"
  rm -rf "$WORK/x264"
  git clone --depth 1 --branch "$X264_TAG" https://code.videolan.org/videolan/x264.git "$WORK/x264"
  (
    cd "$WORK/x264"
    # `--disable-cli` because only the library is wanted, and the command line
    # tool is what pulls in the dependencies that do not cross-compile.
    ./configure \
      --prefix="$PREFIX" \
      --host="$TRIPLE" \
      --enable-static \
      --enable-pic \
      --disable-cli \
      --disable-opencl \
      --extra-cflags="$CFLAGS"
    make -j"$JOBS"
    make install
  )
fi

# ── freetype ──────────────────────────────────────────────────────────────────
if [ ! -f "$PREFIX/lib/libfreetype.a" ]; then
  echo "══ freetype"
  fetch "freetype-$FREETYPE_VERSION" \
        "https://download.savannah.gnu.org/releases/freetype/freetype-$FREETYPE_VERSION.tar.xz" \
        "https://downloads.sourceforge.net/project/freetype/freetype2/$FREETYPE_VERSION/freetype-$FREETYPE_VERSION.tar.xz"
  (
    cd "$WORK/freetype-$FREETYPE_VERSION"
    # Everything optional is off: this build renders glyphs from a .ttf and does
    # nothing else, and each `--with` here is a library that would have to be
    # cross-compiled first.
    ./configure \
      --prefix="$PREFIX" \
      --host="$TRIPLE" \
      --enable-static --disable-shared \
      --with-zlib=no --with-bzip2=no --with-png=no \
      --with-harfbuzz=no --with-brotli=no
    make -j"$JOBS"
    make install
  )
fi

# ── harfbuzz ──────────────────────────────────────────────────────────────────
# Built after freetype and against it, because libass asks harfbuzz to shape
# glyphs that freetype loaded. 2.9.1 is deliberate: every release after it is
# meson-only.
if [ ! -f "$PREFIX/lib/libharfbuzz.a" ]; then
  echo "══ harfbuzz"
  fetch "harfbuzz-$HARFBUZZ_VERSION" \
        "https://github.com/harfbuzz/harfbuzz/releases/download/$HARFBUZZ_VERSION/harfbuzz-$HARFBUZZ_VERSION.tar.xz"
  (
    cd "$WORK/harfbuzz-$HARFBUZZ_VERSION"
    ./configure \
      --prefix="$PREFIX" \
      --host="$TRIPLE" \
      --enable-static --disable-shared \
      --with-freetype=yes \
      --with-glib=no --with-gobject=no --with-cairo=no \
      --with-icu=no --with-graphite2=no
    make -j"$JOBS"
    make install
  )
fi

# ── fribidi ───────────────────────────────────────────────────────────────────
if [ ! -f "$PREFIX/lib/libfribidi.a" ]; then
  echo "══ fribidi"
  fetch "fribidi-$FRIBIDI_VERSION" \
        "https://github.com/fribidi/fribidi/releases/download/v$FRIBIDI_VERSION/fribidi-$FRIBIDI_VERSION.tar.xz"
  (
    cd "$WORK/fribidi-$FRIBIDI_VERSION"
    # fribidi builds table generators and runs them during the build, so it
    # needs a compiler for *this* machine as well as one for the phone. Naming
    # the build flags explicitly keeps the cross flags above from leaking into
    # a program that has to run here.
    ./configure \
      --prefix="$PREFIX" \
      --host="$TRIPLE" \
      --enable-static --disable-shared \
      --disable-debug --disable-deprecated \
      CC_FOR_BUILD=cc \
      CFLAGS_FOR_BUILD="-O2" \
      CPPFLAGS_FOR_BUILD="" \
      LDFLAGS_FOR_BUILD=""
    make -j"$JOBS"
    make install
  )
fi

# ── libass ────────────────────────────────────────────────────────────────────
if [ ! -f "$PREFIX/lib/libass.a" ]; then
  echo "══ libass"
  fetch "libass-$LIBASS_VERSION" \
        "https://github.com/libass/libass/releases/download/$LIBASS_VERSION/libass-$LIBASS_VERSION.tar.gz"
  (
    cd "$WORK/libass-$LIBASS_VERSION"
    # `--disable-require-system-font-provider` is the one that matters: without
    # it configure refuses a build that has no way to look a font family up, and
    # the directory provider this app uses is exactly that build. libunibreak
    # only improves line breaking for scripts that have no spaces.
    ./configure \
      --prefix="$PREFIX" \
      --host="$TRIPLE" \
      --enable-static --disable-shared \
      --disable-fontconfig \
      --disable-libunibreak \
      --disable-require-system-font-provider
    make -j"$JOBS"
    make install
  )
fi

# ── ffmpeg ────────────────────────────────────────────────────────────────────
echo "══ ffmpeg"
fetch "ffmpeg-$FFMPEG_VERSION" \
      "https://ffmpeg.org/releases/ffmpeg-$FFMPEG_VERSION.tar.xz" \
      "https://github.com/FFmpeg/FFmpeg/archive/refs/tags/n$FFMPEG_VERSION.tar.gz"
(
  cd "$WORK/ffmpeg-$FFMPEG_VERSION"
  # `ffbuild/config.mak` is written at the very end of a successful configure,
  # so it is the marker that says "this tree is configured" — `config.h` is
  # written earlier and a configure that failed after it would be skipped.
  [ -f ffbuild/config.mak ] || ./configure \
    --prefix="$PREFIX" \
    --target-os=android \
    --arch="$ARCH" \
    --cpu=armv8-a \
    --enable-cross-compile \
    --cross-prefix="$TOOLCHAIN/bin/llvm-" \
    --cc="$CC" --cxx="$CXX" --ar="$AR" --ranlib="$RANLIB" --nm="$NM" --strip="$STRIP" \
    --sysroot="$SYSROOT" \
    --pkg-config=pkg-config --pkg-config-flags=--static \
    --extra-cflags="$CFLAGS" \
    --extra-ldflags="$LDFLAGS -static-libstdc++" \
    --extra-libs="-lm" \
    --enable-static --disable-shared \
    --enable-pic \
    --enable-gpl --enable-version3 \
    --enable-libx264 \
    --enable-libass --enable-libfreetype \
    --disable-doc --disable-debug \
    --disable-avdevice --disable-indevs --disable-outdevs \
    --disable-postproc \
    --disable-vulkan \
    --disable-ffplay \
    --enable-ffmpeg --enable-ffprobe
  make -j"$JOBS"
)

cp "$WORK/ffmpeg-$FFMPEG_VERSION/ffmpeg" "$OUT_DIR/libffmpeg.so"
cp "$WORK/ffmpeg-$FFMPEG_VERSION/ffprobe" "$OUT_DIR/libffprobe.so"
"$STRIP" "$OUT_DIR/libffmpeg.so" "$OUT_DIR/libffprobe.so"

echo
echo "built into $OUT_DIR:"
ls -lh "$OUT_DIR"
file "$OUT_DIR/libffmpeg.so" || true
