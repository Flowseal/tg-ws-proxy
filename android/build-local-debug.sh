#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

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
TASK="${1:-assembleDebug}"

for attempt in $(seq 1 "$ATTEMPTS"); do
  echo "==> Android build attempt $attempt/$ATTEMPTS ($TASK)"
  if "$GRADLE_BIN" --no-daemon --console=plain "$TASK"; then
    exit 0
  fi

  if [[ "$attempt" -lt "$ATTEMPTS" ]]; then
    echo "Build failed, retrying in ${SLEEP_SECONDS}s..."
    sleep "$SLEEP_SECONDS"
  fi
done

echo "Android build failed after $ATTEMPTS attempts."
exit 1
