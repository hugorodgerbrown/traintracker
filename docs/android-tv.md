# The TV app

`android-tv/` is Traintrackr for Google TV and Android TV (Chromecast with
Google TV, Google TV Streamer, Android TVs): a home-screen tile that opens
straight onto a departure board. Why it is built this way:
[docs/decisions/a-tv-picks-its-board-from-a-phone.md](decisions/a-tv-picks-its-board-from-a-phone.md).

For a TV that isn't Android, open a board's TV link (`/board/LST?tv=1`) in
its browser, or cast a Chrome tab of it.

## Using it

1. Open **Traintrackr** on the TV. It shows a code like `K7Q-4MD` and a QR code.
2. On your phone, scan the QR code, or go to `traintrackr.live/tv` and type the code.
   Choose one of your boards, or type a station. You don't need to sign in.
3. The TV shows the board within a few seconds, and opens straight to it from then on.

A board's own page links to `/tv` with that board chosen, for a platform's
board or one to a destination.

Press **Back** or **Menu** on the remote for the app's menu: keep
watching, choose a different board, start when the TV turns on, or exit.

## Installing it

The app isn't on Google Play; you install the APK yourself.

1. Get the APK from the latest `tv-v*` [release](../../releases), or from
   the **traintrackr-tv** artifact of an **Android TV** workflow run.
2. On the TV: Settings → System → About → tap **Android TV OS build**
   seven times to turn on developer options.
3. Then either
   - install **Downloader** from the Play Store, allow it to install
     unknown apps (Settings → Apps → Security & restrictions), and open
     the APK's URL in it; or
   - turn on **USB debugging** (or **Wireless debugging**) in developer
     options and run `adb connect <tv-ip>` then
     `adb install -r traintrackr-tv-<version>.apk` from a computer.

### Starting when the TV turns on

Turn it on in the app's menu. Android only lets an app start by itself if
it may display over other apps, and Google TV usually hides that setting,
so allow it once from a computer:

```bash
adb shell appops set live.traintrackr.tv SYSTEM_ALERT_WINDOW allow
```

## Building it

You need JDK 17 and the Android SDK (Android Studio brings both).

```bash
cd android-tv
./gradlew testDebugUnitTest lintDebug assembleDebug
./gradlew installDebug -PserverUrl=http://10.0.2.2:8000   # an emulator, against runserver
```

`serverUrl` in `android-tv/gradle.properties` is the server the app pairs
with (`https://traintrackr.live`). Only debug builds may use plain `http://`; release builds are HTTPS only.

CI (`.github/workflows/android-tv.yml`) runs the same tasks on every
change under `android-tv/` and uploads the APK.

### Releasing

On GitHub, open **Actions → Android TV → Run workflow**, leave the branch
on `main`, type the version (three numbers, like `0.1.0`) and run it. It
tests and builds the APK, then publishes the release **Traintrackr for
Android TV 0.1.0** with `traintrackr-tv-0.1.0.apk` attached, tagging the
commit it built `tv-v0.1.0`. Pushing a `tv-v0.1.0` tag yourself does the
same. Each version can be released once.

### The signing key

Android only installs an update signed with the same key as the app
already on the TV. Make one key, keep it safe, and give it to CI as
repository secrets:

```bash
keytool -genkeypair -v -keystore traintrackr-tv.jks -alias traintrackr-tv \
  -keyalg RSA -keysize 4096 -validity 10000
base64 -w0 traintrackr-tv.jks   # → TRAINTRACKR_KEYSTORE_BASE64
```

| Secret | Value |
| --- | --- |
| `TRAINTRACKR_KEYSTORE_BASE64` | the keystore, base64-encoded |
| `TRAINTRACKR_KEYSTORE_PASSWORD` | its password |
| `TRAINTRACKR_KEY_ALIAS` | `traintrackr-tv` |
| `TRAINTRACKR_KEY_PASSWORD` | the key's password |

Without them, builds are signed with a throwaway debug key, and each one
must be uninstalled before the next goes on.
