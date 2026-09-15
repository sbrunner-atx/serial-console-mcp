#!/bin/bash
# Build a (optionally signed + notarized) macOS .pkg.
#
# Unsigned local/CI build:   ./build_macos.sh
# Signed + notarized build (on your Mac with your Developer ID):
#   SIGN_IDENTITY_APP="Developer ID Application: Your Name (TEAMID)" \
#   SIGN_IDENTITY_INSTALLER="Developer ID Installer: Your Name (TEAMID)" \
#   NOTARY_PROFILE="AC"  ./build_macos.sh
set -euo pipefail

APP_NAME="ham-serial-mcp"
VERSION="${VERSION:-1.0}"
IDENTIFIER="${IDENTIFIER:-com.yourcall.hamserialmcp}"
INSTALL_LOCATION="/Library/Application Support/HamSerialMCP"

python3 -m venv .venv
# shellcheck disable=SC1091
source .venv/bin/activate
pip install --quiet -r requirements.txt pyinstaller
pyinstaller --onefile --name "$APP_NAME" --collect-all mcp ham_serial_mcp.py

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
  "HamSerialMCP-component.pkg"

productbuild --package "HamSerialMCP-component.pkg" "HamSerialMCP-$VERSION.pkg"

if [ -n "${SIGN_IDENTITY_INSTALLER:-}" ]; then
  productsign --sign "$SIGN_IDENTITY_INSTALLER" \
    "HamSerialMCP-$VERSION.pkg" "HamSerialMCP-$VERSION-signed.pkg"
  mv "HamSerialMCP-$VERSION-signed.pkg" "HamSerialMCP-$VERSION.pkg"
  if [ -n "${NOTARY_PROFILE:-}" ]; then
    xcrun notarytool submit "HamSerialMCP-$VERSION.pkg" \
      --keychain-profile "$NOTARY_PROFILE" --wait
    xcrun stapler staple "HamSerialMCP-$VERSION.pkg"
  fi
else
  echo "WARN: SIGN_IDENTITY_INSTALLER not set -> unsigned .pkg (Gatekeeper will warn)."
fi

echo "Built HamSerialMCP-$VERSION.pkg"
