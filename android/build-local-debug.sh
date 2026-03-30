#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_BUILD_DIR="$ROOT_DIR/app/build"

if [[ -z "${GRADLE_USER_HOME:-}" ]]; then
  if [[ -d "$HOME/.gradle" && -w "$HOME/.gradle" ]]; then
    export GRADLE_USER_HOME="$HOME/.gradle"
  else
    export GRADLE_USER_HOME="$ROOT_DIR/.gradle-local"
  fi
fi

mkdir -p "$GRADLE_USER_HOME"

if [[ -d "$HOME/.local/jdk" ]]; then
  export JAVA_HOME="$HOME/.local/jdk"
fi

if [[ -d "$HOME/android-sdk" ]]; then
  export ANDROID_SDK_ROOT="$HOME/android-sdk"
fi

if [[ -n "${JAVA_HOME:-}" ]]; then
  export PATH="$JAVA_HOME/bin:$PATH"
fi

if [[ -n "${ANDROID_SDK_ROOT:-}" ]]; then
  export PATH="$ANDROID_SDK_ROOT/cmdline-tools/latest/bin:$ANDROID_SDK_ROOT/platform-tools:$PATH"
fi

if [[ -d "$HOME/.local/gradle/gradle-8.7/bin" ]]; then
  export PATH="$HOME/.local/gradle/gradle-8.7/bin:$PATH"
fi

unset HTTP_PROXY HTTPS_PROXY ALL_PROXY http_proxy https_proxy all_proxy

GRADLE_BIN="gradle"
if [[ -x "$ROOT_DIR/gradlew" ]]; then
  GRADLE_BIN="$ROOT_DIR/gradlew"
fi

ATTEMPTS="${ATTEMPTS:-5}"
SLEEP_SECONDS="${SLEEP_SECONDS:-15}"
TASK="${1:-assembleStandardDebug}"
LOCAL_CHAQUOPY_REPO="${LOCAL_CHAQUOPY_REPO:-$ROOT_DIR/.m2-chaquopy}"
CHAQUOPY_MAVEN_BASE="${CHAQUOPY_MAVEN_BASE:-https://repo.maven.apache.org/maven2}"

running_on_wsl_windows_mount() {
  [[ -n "${WSL_DISTRO_NAME:-}" && "$ROOT_DIR" == /mnt/* ]]
}

prepare_wsl_build_dir() {
  if ! running_on_wsl_windows_mount; then
    return 0
  fi

  local cache_root="${XDG_CACHE_HOME:-$HOME/.cache}/tg-ws-proxy-android-build"
  local project_key
  project_key="$(printf '%s' "$ROOT_DIR" | sha256sum | cut -d' ' -f1)"
  local linux_build_dir="$cache_root/$project_key/app-build"

  mkdir -p "$cache_root"
  mkdir -p "$linux_build_dir"

  if [[ -L "$APP_BUILD_DIR" ]]; then
    return 0
  fi

  if [[ -e "$APP_BUILD_DIR" ]]; then
    rm -rf "$APP_BUILD_DIR"
  fi

  ln -s "$linux_build_dir" "$APP_BUILD_DIR"
}

task_uses_legacy32() {
  [[ "$TASK" =~ [Ll]egacy32 ]]
}

task_uses_standard() {
  if [[ "$TASK" =~ [Ss]tandard ]]; then
    return 0
  fi

  if task_uses_legacy32; then
    return 1
  fi

  return 0
}

prefetch_artifact() {
  local relative_path="$1"
  local destination="$LOCAL_CHAQUOPY_REPO/$relative_path"

  if [[ -f "$destination" ]]; then
    return 0
  fi

  mkdir -p "$(dirname "$destination")"
  echo "Prefetching $relative_path"
  curl \
    --fail \
    --location \
    --retry 8 \
    --retry-all-errors \
    --continue-at - \
    --connect-timeout 15 \
    --speed-limit 1024 \
    --speed-time 20 \
    --max-time 90 \
    --output "$destination" \
    "$CHAQUOPY_MAVEN_BASE/$relative_path"
}

prefetch_chaquopy_runtime() {
  local artifacts=(
    "com/chaquo/python/runtime/chaquopy_java/17.0.0/chaquopy_java-17.0.0.pom"
    "com/chaquo/python/runtime/chaquopy_java/17.0.0/chaquopy_java-17.0.0.jar"
    "com/chaquo/python/runtime/libchaquopy_java/17.0.0/libchaquopy_java-17.0.0.pom"
  )

  if task_uses_standard; then
    artifacts+=(
      "com/chaquo/python/runtime/libchaquopy_java/17.0.0/libchaquopy_java-17.0.0-3.12-arm64-v8a.so"
      "com/chaquo/python/runtime/libchaquopy_java/17.0.0/libchaquopy_java-17.0.0-3.12-x86_64.so"
      "com/chaquo/python/target/3.12.12-0/target-3.12.12-0.pom"
      "com/chaquo/python/target/3.12.12-0/target-3.12.12-0-arm64-v8a.zip"
      "com/chaquo/python/target/3.12.12-0/target-3.12.12-0-stdlib-pyc.zip"
      "com/chaquo/python/target/3.12.12-0/target-3.12.12-0-stdlib.zip"
      "com/chaquo/python/target/3.12.12-0/target-3.12.12-0-x86_64.zip"
    )
  fi

  if task_uses_legacy32; then
    artifacts+=(
      "com/chaquo/python/runtime/libchaquopy_java/17.0.0/libchaquopy_java-17.0.0-3.11-armeabi-v7a.so"
      "com/chaquo/python/target/3.11.10-0/target-3.11.10-0.pom"
      "com/chaquo/python/target/3.11.10-0/target-3.11.10-0-armeabi-v7a.zip"
      "com/chaquo/python/target/3.11.10-0/target-3.11.10-0-stdlib-pyc.zip"
      "com/chaquo/python/target/3.11.10-0/target-3.11.10-0-stdlib.zip"
    )
  fi

  for artifact in "${artifacts[@]}"; do
    prefetch_artifact "$artifact"
  done
}

cleanup_stale_build_state() {
  local stale_dirs=(
    "$APP_BUILD_DIR/python/env"
    "$APP_BUILD_DIR/intermediates/project_dex_archive"
    "$APP_BUILD_DIR/intermediates/desugar_graph"
    "$APP_BUILD_DIR/tmp/kotlin-classes"
    "$APP_BUILD_DIR/snapshot/kotlin"
  )

  for stale_dir in "${stale_dirs[@]}"; do
    if [[ -d "$stale_dir" ]]; then
      rm -rf "$stale_dir"
    fi
  done
}

prefetch_chaquopy_runtime
prepare_wsl_build_dir

for attempt in $(seq 1 "$ATTEMPTS"); do
  echo "==> Android build attempt $attempt/$ATTEMPTS ($TASK)"
  if "$GRADLE_BIN" --no-daemon --console=plain "$TASK"; then
    exit 0
  fi

  if [[ "$attempt" -lt "$ATTEMPTS" ]]; then
    cleanup_stale_build_state
    echo "Build failed, retrying in ${SLEEP_SECONDS}s..."
    sleep "$SLEEP_SECONDS"
  fi
done

echo "Android build failed after $ATTEMPTS attempts."
exit 1
