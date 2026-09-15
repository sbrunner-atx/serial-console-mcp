#!/bin/bash
# Build a (optionally signed + notarized) macOS .pkg.
#
# Unsigned local/CI build:   ./build_macos.sh
# Signed + notarized build (on your Mac with your Developer ID):
#   SIGN_IDENTITY_APP="Developer ID Application: Your Name (TEAMID)" \
#   SIGN_IDENTITY_INSTALLER="Developer ID Installer: Your Name (TEAMID)" \
#   NOTARY_PROFILE="AC"  ./build_macos.sh
set -euo pipefail

APP_NAME="serial-console-mcp"
VERSION="${VERSION:-0.2.0}"
IDENTIFIER="${IDENTIFIER:-org.stefanbrunner.serialconsolemcp}"
INSTALL_LOCATION="/Library/Application Support/SerialConsoleMCP"

python3 -m venv .venv
# shellcheck disable=SC1091
source .venv/bin/activate
pip install --quiet ".[freeze]"
pyinstaller --onefile --name "$APP_NAME" --collect-all mcp --collect-all serial_console_mcp packaging/entry.py

# Sign the Mach-O with hardened runtime (required for notarization).
if [ -n "${SIGN_IDENTITY_APP:-}" ]; then
  codesign --force --options runtime --timestamp \
    --sign "$SIGN_IDENTITY_APP" "dist/$APP_NAME"
else
  echo "WARN: SIGN_IDENTITY_APP not set -> unsigned binary (fine for testing)."
fi

rm -rf pkgroot
mkdir -p "pkgroot${INSTALL_LOCATION}"
cp "dist/$APP_NAME" "pkgroot${INSTALL_LOCATION}/"
chmod +x "pkgroot${INSTALL_LOCATION}/$APP_NAME"
chmod +x scripts/postinstall

pkgbuild --root pkgroot \
  --identifier "$IDENTIFIER" \
  --version "$VERSION" \
  --scripts scripts \
  --install-location "/" \
  "SerialConsoleMCP-component.pkg"

productbuild --package "SerialConsoleMCP-component.pkg" "SerialConsoleMCP-$VERSION.pkg"
rm -f "SerialConsoleMCP-component.pkg"  # intermediate; keep only the product pkg

if [ -n "${SIGN_IDENTITY_INSTALLER:-}" ]; then
  productsign --sign "$SIGN_IDENTITY_INSTALLER" \
    "SerialConsoleMCP-$VERSION.pkg" "SerialConsoleMCP-$VERSION-signed.pkg"
  mv "SerialConsoleMCP-$VERSION-signed.pkg" "SerialConsoleMCP-$VERSION.pkg"
  if [ -n "${NOTARY_PROFILE:-}" ]; then
    xcrun notarytool submit "SerialConsoleMCP-$VERSION.pkg" \
      --keychain-profile "$NOTARY_PROFILE" --wait
    xcrun stapler staple "SerialConsoleMCP-$VERSION.pkg"
  fi
else
  echo "WARN: SIGN_IDENTITY_INSTALLER not set -> unsigned .pkg (Gatekeeper will warn)."
fi

echo "Built SerialConsoleMCP-$VERSION.pkg"
