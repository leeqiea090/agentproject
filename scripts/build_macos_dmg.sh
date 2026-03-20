#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
APP_BUNDLE_NAME="${APP_BUNDLE_NAME:-BidAgent}"
APP_PATH="${APP_PATH:-$ROOT_DIR/dist/${APP_BUNDLE_NAME}.app}"
DMG_NAME="${DMG_NAME:-${APP_BUNDLE_NAME}.dmg}"
DMG_PATH="${DMG_PATH:-$ROOT_DIR/dist/${DMG_NAME}}"
VOL_NAME="${VOL_NAME:-${APP_BUNDLE_NAME} Installer}"
SKIP_APP_BUILD="${SKIP_APP_BUILD:-0}"

cd "$ROOT_DIR"

if ! command -v hdiutil >/dev/null 2>&1; then
    echo "未找到 hdiutil，当前系统无法生成 macOS DMG。" >&2
    exit 1
fi

if [[ "$SKIP_APP_BUILD" != "1" ]]; then
    "$ROOT_DIR/scripts/build_macos_app.sh"
fi

if [[ ! -d "$APP_PATH" ]]; then
    echo "未找到 App 产物: $APP_PATH" >&2
    echo "请先执行 ./scripts/build_macos_app.sh，或通过 APP_PATH 指定现有 .app 路径。" >&2
    exit 1
fi

STAGE_DIR="$(mktemp -d "${TMPDIR:-/tmp}/${APP_BUNDLE_NAME}-dmg-stage.XXXXXX")"

cleanup() {
    rm -rf "$STAGE_DIR"
}

trap cleanup EXIT

rm -f "$DMG_PATH"
cp -R "$APP_PATH" "$STAGE_DIR/"
ln -s /Applications "$STAGE_DIR/Applications"

hdiutil create \
    -volname "$VOL_NAME" \
    -srcfolder "$STAGE_DIR" \
    -ov \
    -format UDZO \
    "$DMG_PATH"

echo
echo "DMG 构建完成:"
echo "  $DMG_PATH"
echo
echo "安装方式:"
echo "  打开 DMG 后，将 ${APP_BUNDLE_NAME}.app 拖到 Applications。"
