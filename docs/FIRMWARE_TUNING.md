# T2M+ firmware: TvService CPU and USB/Wi-Fi

**English** · [Українська](FIRMWARE_TUNING.uk.md) · [Apps and setup](PROJECTOR_SETUP.md)

These findings apply to one Wanbo T2M+ running Android 9 / API 28, firmware `9.0.0-RS-20210928.1224`, with 32-bit ARM userspace. Different T2 Max revisions can use different boards, partitions and security policies. Matching the product name alone is insufficient.

The web remote works independently of these optional firmware changes. Docker and the browser never install the patch, remount Android partitions or change USB/Wi-Fi settings. This repository provides the findings and a source-diff reference, without OEM APKs, firmware images, signing keys or an automatic firmware installer.

## Measured result

| Observation | Result on the tested unit |
| --- | --- |
| Stock TvService CPU | Approximately 103% of one core in a 5.4-second sample; the temperature thread accounted for about 94% |
| Modified TvService CPU | Median 0.19%, maximum 0.26% of one core during 15 minutes |
| Sampled availability | 57 successful Wi-Fi/ADB checks; the service PID and start time stayed unchanged |
| Temperature property | 38–46°C during the observation |
| Persistence | Modified APK and USB `host` configuration were present after a completed new boot |

CPU percentages are per core; 100% means one busy core, not the entire four-core processor. The observation included normal Jellyfin viewing. Checks were read-only: they did not pause playback, send remote keys or restart applications. This is sampled validation on one device, not continuous packet-loss measurement or a multi-hour test. Real overheating was not induced.

## Read-only diagnosis

Use the authenticated ADB shell described in the [setup guide](PROJECTOR_SETUP.md#adb-shell-for-the-commands-below). These checks do not alter the projector:

```sh
adb shell getprop ro.product.model
adb shell getprop ro.build.version.sdk
adb shell getprop ro.product.cpu.abi
adb shell 'dumpsys cpuinfo | grep com.toptech.tv.service'
adb shell 'tr " " "\n" < /proc/cmdline | grep "^USB_OTG_SOFTWARE_ID="'
adb shell 'grep -E "^(cfg80211|8723du) " /proc/modules'
adb shell ip link show wlan0
adb shell sha256sum /system/app/TvService/TvService.apk
```

Keep raw logs, serials, MAC addresses, local endpoints and account information private. Android root access is separate from container permissions. On this build, `/system` lived on the root (`/`) filesystem and `/vendor` was a separate mount; verify the actual mountpoints. The bundled command-line tools also differ from desktop GNU tools: this build's `stat` does not implement `%r`; that format returned `?`. Mount checks used `/proc/self/mountinfo` and verified device identities instead.

## TvService: a pause in the temperature loop

The factory `com.toptech.tv.service.TvService$5.run()` repeatedly read `mid.data.about.temperature` without sleeping. Besides consuming a core, this created allocation/GC work. The functional change was **`SystemClock.sleep(100L)` at the common loop back-edge**, after the existing check. The first temperature check remains immediate; subsequent iterations have a 100 ms pause. The existing **80°C protection branch is retained**.

The exact reference change is [tvservice-temperature-sleep.patch](../patches/tvservice-temperature-sleep.patch). It targets a disassembly of the following original APK:

```text
SHA-256: a596d7f33fab2723b20870ab1f7a415be6f7df7f7266cc641c2ab7534043181b
Package: com.toptech.tv.service
Path: /system/app/TvService/TvService.apk
```

The diff is not a compiled update or deployment script. Disassembly labels and registers can differ on other releases. Do not apply it to a guessed match, disable the whole service, or remove its hardware/temperature handling.

### Signing and system integration

Changing `classes.dex` invalidates the original APK's content signature. The tested package shares `android.uid.system`, so signing it with an unrelated key is not a normal replacement. Ordinary verification rejected the modified content, as expected.

Android 9 has a trusted-system certificate-collection path during [system-package scanning](https://android.googlesource.com/platform/frameworks/base/+/android-9.0.0_r1/services/core/java/com/android/server/pm/PackageManagerService.java#8831). On this unit, a read-only parser probe accepted the original package normally and the candidate through that system path, preserving package identity, shared UID and OEM certificate metadata. A subsequent actual boot registered and ran the modified persistent system service. **A parser probe alone would not have proved boot safety.**

This does not produce a valid OEM-signed update or recover the OEM private key. The tested installation was a controlled root modification of the system partition, with the original APK retained for rollback. Global APK verification was not disabled. Other firmware may reject the same approach.

## USB boot role and disappearing Wi-Fi

The internal Wi-Fi device was a USB Realtek `0bda:d723`, using `8723du` and `cfg80211`. In a failing boot, the adapter, modules and `wlan0` were absent; the supplicant then failed because the `nl80211` interface was unavailable. That was a hardware/driver detection problem, before Wi-Fi credentials were involved.

The vendor boot configuration connects USB debugging and the next boot's USB role:

| File | Factory content or purpose |
| --- | --- |
| `/vendor/etc/adb_enable_on.ini` | `[ADB_USB]` with `BOOT_ARGS = "USB_OTG_SOFTWARE_ID=device";` |
| `/vendor/etc/adb_enable_off.ini` | `[ADB_USB]` with `BOOT_ARGS = "USB_OTG_SOFTWARE_ID=host";` |
| `/vendor/tvconfig/config/adb/adb_enable.ini` | Persistent boot-role configuration copied from those sources by vendor init |

When `sys.usb.config` became `adb`, vendor init copied the **ON** source into the live boot-role file. A `device`-mode boot hid the internal Wi-Fi adapter on this unit. A completed `host`-mode boot restored adapter detection, the drivers, Wi-Fi and network ADB.

Writing `host` only to the live file was temporary: Android copied the factory `device` value back during startup. The persistent change tested here used the factory **OFF/host bytes for both the ON source and the live file**, preserving their ownership, modes and SELinux contexts. Existing network-ADB properties stayed unchanged; the vendor partition was returned to read-only before the boot test. Both files still contained `host` after startup.

This changes the USB-role policy. Direct USB ADB was not established on this unit; it must not be assumed to provide recovery. Clearing debugging flags or switching USB configurations blindly can also remove the remaining network/local-ADB path. These findings do not diagnose every Wi-Fi failure on other boards.

## Deployment and recovery requirements

Firmware writes are separate maintenance work. Pause playback and agree on the interruption before restarting a hardware service or the projector. The read-only diagnostics above can be used while watching.

1. Confirm the exact firmware, source hashes, Android root access, mount topology and package/shared-UID compatibility. Keep an offline recovery route and physical access available before a write.
2. Back up the original APK and all affected INIs, including their owner, mode, SELinux context and original mount flags. A factory reset clears user data; it does not restore modified system/vendor files. An image backup alone does not prove that emergency flashing works.
3. Use same-directory temporary files, verify their hashes and metadata, then rename atomically. For the APK, a process holding the old unlinked inode can prevent an ext4 read-only remount; use a bounded restart of the verified TvService instance before confirming read-only state. The tested deployment had independent runtime rollback guards; those processes do not survive reboot.
4. Inspect both hashes and mount flags after any failure. A late read-only-remount error can leave the ON source changed while the live INI is still old. Do not describe that as an unchanged device, blindly rerun an old-hash guard, or reboot before reconciling the known state. Keep the original APK recoverable independently of Wi-Fi.
5. Verify a genuinely new boot, package/service registration, CPU, Wi-Fi/ADB and the final INIs, permissions and read-only mounts. Observe normal use afterward. A successful command or certificate-only parse is insufficient.

Automatic return after **`adb reboot` was not established**. A new boot was eventually verified, but the available evidence does not establish whether physical power assistance was required. Plan for that limitation; do not test reboots during playback.

The persistent CPU fix runs on the projector itself. Once installed and verified on the tested unit, it does not depend on keeping the browser, Docker container or a laptop monitoring process running. This result does not establish compatibility with a different firmware or native Linux.
