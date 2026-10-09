#!/bin/sh
# Builds ArrowBotHelper.apk (repo root) without Gradle: aapt2, javac, d8, zipalign, apksigner.
#   BUILD_TOOLS=/path/to/build-tools/36.0.0 ANDROID_JAR=/path/to/platforms/android-35/android.jar sh helper/build_apk.sh
# The signing key (helper/helper.keystore) is made on the first build and kept: an update has to be
# signed with the same key to install over the old app.
set -e
HERE=$(cd "$(dirname "$0")" && pwd)
OUT="$HERE/../ArrowBotHelper.apk"
: "${BUILD_TOOLS:?set BUILD_TOOLS to an Android build-tools folder}"
: "${ANDROID_JAR:?set ANDROID_JAR to platforms/android-35/android.jar}"
VERSION_CODE=1
VERSION_NAME=1.0

W=$(mktemp -d)
trap 'rm -rf "$W"' EXIT
mkdir -p "$W/res" "$W/gen" "$W/classes" "$W/dex"

"$BUILD_TOOLS/aapt2" compile --dir "$HERE/res" -o "$W/res/res.zip"
"$BUILD_TOOLS/aapt2" link -o "$W/base.apk" -I "$ANDROID_JAR" --manifest "$HERE/AndroidManifest.xml" \
    --java "$W/gen" --min-sdk-version 26 --target-sdk-version 35 \
    --version-code $VERSION_CODE --version-name $VERSION_NAME "$W/res/res.zip"

javac -nowarn -source 11 -target 11 -encoding UTF-8 -classpath "$ANDROID_JAR" -d "$W/classes" \
    $(find "$W/gen" "$HERE/src" -name '*.java') 2>&1 | grep -v "^warning: \[options\]\|^Picked up\|^1 warning$" || true
test -n "$(find "$W/classes" -name '*.class')" || { echo "javac failed"; exit 1; }

"$BUILD_TOOLS/d8" --release --min-api 26 --lib "$ANDROID_JAR" --output "$W/dex" \
    $(find "$W/classes" -name '*.class') 2>&1 | grep -v "^Picked up" || true
(cd "$W/dex" && zip -q -j "$W/base.apk" classes.dex)

"$BUILD_TOOLS/zipalign" -p -f 4 "$W/base.apk" "$W/aligned.apk"

KS="$HERE/helper.keystore"
if [ ! -f "$KS" ]; then
    keytool -genkeypair -keystore "$KS" -storepass arrowbot -keypass arrowbot -alias helper \
        -keyalg RSA -keysize 2048 -validity 36500 -dname "CN=ArrowBot Helper" 2>&1 | grep -v "^Picked up" || true
fi
"$BUILD_TOOLS/apksigner" sign --ks "$KS" --ks-pass pass:arrowbot --key-pass pass:arrowbot \
    --out "$OUT" "$W/aligned.apk" 2>&1 | grep -v "^Picked up" || true
"$BUILD_TOOLS/apksigner" verify "$OUT" 2>&1 | grep -v "^Picked up" || true
echo "[+] $(cd "$(dirname "$OUT")" && pwd)/$(basename "$OUT") ($(du -k "$OUT" | cut -f1) KB)"
