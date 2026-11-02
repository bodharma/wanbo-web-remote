# Wanbo T2 Max: apps and performance

**English** · [Українська](PROJECTOR_SETUP.uk.md) · [Remote setup](../README.md#setup)

These notes come from one T2M+ running Android 9, build `9.0.0-RS-20210928.1224`, with 1 GB nominal RAM and 32-bit ARM userspace. They describe observed results, not a universal debloat recipe. Other T2 Max revisions, especially Android 6 models, can have different hardware and firmware.

## Apps to install

Install only what you use. Follow the linked project's current compatibility and security information; the versions tested here are a reference, not a recommendation to stay on old releases. APKs can be installed through the projector's USB file manager. This repository does not redistribute them.

| App / source | Purpose and tested configuration |
| --- | --- |
| [Developer Tools by Roy Solberg](https://play.google.com/store/apps/details?id=com.roysolberg.android.developertools) | Access Android information and Developer options when the stock menus hide them. Package: `com.roysolberg.android.developertools`. Opening Developer options does not itself enable network ADB. |
| [Lite TV Launcher](https://github.com/anujmv/lite-tv-launcher/releases) | A lightweight Home replacement; version 1.0 was tested. Package: `dev.lite.launcher`. Install and test before changing the stock launcher. |
| [SmartTube F-Droid](https://f-droid.org/en/packages/app.smarttube.fdroid/) | YouTube client for TV navigation. Package: `app.smarttube.fdroid`, matching the remote's shortcut. Use the F-Droid build channel; other SmartTube variants have different package IDs and signatures. Read the [upstream security announcement](https://github.com/yuliskov/SmartTube#important-announcement-about-the-app): the developer reported a compromised build environment and potentially affected builds/keys. A working player alone does not establish that a build is safe. |
| [Jellyfin for Android TV](https://github.com/jellyfin/jellyfin-androidtv/releases) | Browse your own Jellyfin server; version 0.19.10 was tested. Package: `org.jellyfin.androidtv`. Enter your server's LAN address and use its normal sign-in or Quick Connect. |
| [Kodi](https://kodi.tv/download/android/) | An external Jellyfin player and a software-decoding fallback. Choose **ARMV7A (32BIT)** for this unit; version 20.2 was tested. Package: `org.xbmc.kodi`. This does not mean every newer release has been tested on this firmware. |

Keep the package IDs above if you want the existing Jellyfin / SmartTube / Kodi shortcuts to work. A different variant requires changing the `APPS` allowlist in `web_remote.py` and building from source.

## ADB shell for the commands below

Complete the [remote setup](../README.md#setup) first, including the projector identity check. From the repository directory, open a temporary shell using the same ADB-key volume:

```sh
docker compose run --rm --no-deps --entrypoint sh remote
adb connect "$PROJECTOR_HOST:$PROJECTOR_PORT"
export ANDROID_SERIAL="$PROJECTOR_HOST:$PROJECTOR_PORT"
adb shell getprop ro.serialno
adb shell getprop ro.product.cpu.abi
adb shell getprop ro.build.version.sdk
```

Check that the serial matches your `.env`. The tested unit reports `armeabi-v7a` and API `28`. Run the remaining `adb` commands inside this shell; `exit` closes it. Do not post its output unredacted: serials, accounts, local addresses and logs can be personal.

## Keep Wi-Fi and recovery available

Before changing Home, confirm that you can still reach native Wi-Fi settings:

```sh
adb shell am start -a android.settings.WIFI_SETTINGS
```

Keep your physical remote and the current ADB connection available. Do not change Wi-Fi, disable Android Settings, or reboot as part of launcher tuning. Do not mass-disable packages: a name that looks like bloat can belong to projector controls.

## Faster transitions

Record the original values somewhere private:

```sh
adb shell settings get global window_animation_scale
adb shell settings get global transition_animation_scale
adb shell settings get global animator_duration_scale
```

Set the three animation scales to `0.5`:

```sh
adb shell settings put global window_animation_scale 0.5
adb shell settings put global transition_animation_scale 0.5
adb shell settings put global animator_duration_scale 0.5
```

This shortens transitions; it does not repair a busy CPU or failing decoder. To undo it, put back the recorded values; use `settings delete global KEY` if the original value was `null`.

## Lighter Home screen

Install Lite TV Launcher, open it, and select it as the default Home application. Test Home, app launching and access to Wi-Fi settings before disabling anything. On the tested unit its observed PSS was approximately 26–46 MiB versus 58–78 MiB for the stock launcher; those are samples, not a benchmark guarantee.

Check the selected Home with both categories present:

```sh
adb shell cmd package resolve-activity --brief \
  -a android.intent.action.MAIN \
  -c android.intent.category.HOME \
  -c android.intent.category.DEFAULT
```

Keep `CATEGORY_DEFAULT`: a HOME-only query disturbed the preferred Home selection on this firmware. The result should point to the replacement launcher.

On this specific firmware a stock Home watcher kept taking over. After the replacement and recovery path were tested, disabling **only** `com.zeasn.whale.open.launcher.technical` for user 0 fixed that behavior without deleting its data:

```sh
adb shell pm disable-user --user 0 com.zeasn.whale.open.launcher.technical
```

This is optional and firmware-specific. Do not substitute a guessed package name. Restore the stock launcher with:

```sh
adb shell pm enable --user 0 com.zeasn.whale.open.launcher.technical
```

## Jellyfin playback through Kodi

First try ordinary playback with hardware decoding. On the tested unit the vendor H.264 decoder `OMX.MS.AVC.Decoder` failed in both Jellyfin and Kodi. The working fallback was:

1. In Kodi, open **Settings → Player → Videos**, switch the settings level to **Advanced** or **Expert**, then find **Processing**.
2. Disable **Allow hardware acceleration — MediaCodec (Surface)** and **Allow hardware acceleration — MediaCodec**.
3. In Jellyfin for Android TV, enable **Playback → Advanced → Use external player**.
4. Start a video from Jellyfin on the projector and select **Kodi → Always** in the Android chooser.
5. Test a short section before a full session. A 720p / 25 fps H.264 + AAC sample played with software decoding here; heavier video, 1080p/4K and external-player progress synchronization were not verified.

Software decoding increases CPU load. If hardware playback works on your unit, keep it enabled. To revert the fallback, enable both Kodi acceleration settings and disable Jellyfin's external-player setting.

Jellyfin Android TV 0.19.10 also crashed when a web client's **Play on** command tried to open the external player (`FLAG_ACTIVITY_NEW_TASK` missing). Local Play worked. The remote's navigation buttons operate the projector UI and avoid that specific remote-launch path; they do not patch Jellyfin. Newer client versions may behave differently. Lowering a bitrate limit does not force transcoding when the file is already below that limit.

## Google Play sign-in loops

Check installed packages rather than assuming a Google component is absent because the launcher hides it:

```sh
adb shell dumpsys package com.android.vending
adb shell dumpsys package com.google.android.gms
adb shell dumpsys package com.google.android.gsf
```

On this unit, updating the old Google Play services installation restored sign-in. That observation does not identify the cause of every sign-in loop. Use a compatible, correctly signed update for the installed Google component and the device's ABI/API; prefer its normal update mechanism. Do not install arbitrary GApps bundles, clear accounts as a first step, or copy Google APKs from this repository. The vendor WebView was signed differently from Google WebView, so an ordinary Google APK was not a drop-in replacement.

## Limits of stock-firmware tuning

`com.toptech.tv.service` / TvService consumed roughly one CPU core in a thermal-monitoring loop on the tested firmware. It also controls projector hardware. **Do not disable the whole service, stop its thermal protection, or replace it with a generic Android TV service.** Restarting it can also change ADB availability. No permanent TvService or firmware patch is included here.

For persistent freezes, collect a short private `adb shell top -b -n 1`, `adb shell dumpsys meminfo` and `adb logcat -d` capture around the failure; identify the process or decoder error before changing more settings. Review and redact logs before sharing them. These changes improve usability within the stock firmware's limits; they do not establish compatibility with another firmware or Linux.
