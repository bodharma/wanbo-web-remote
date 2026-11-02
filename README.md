# Wanbo Web Remote

**English** · [Українська](README.uk.md)

A local browser remote for an Android projector over Wi-Fi and ADB: navigation, volume, Unicode text paste, a relative mouse, and Jellyfin / SmartTube / Kodi shortcuts. Screenshots update on request; there is no continuous video encoding.

The interface supports **English / Українська**. It initially follows the browser language and remembers your choice locally. Switching languages preserves any text you entered.

For apps to install, a lighter home screen, animation settings, and Jellyfin / Kodi playback fixes, see the [projector setup and performance guide](docs/PROJECTOR_SETUP.md).

## Requirements

Docker with Compose v2 and network access from the container to the projector. On macOS, start OrbStack or Docker Desktop. The projector must already expose ADB over TCP, usually port `5555`; Developer options alone do not guarantee this. Mouse support requires ADB shell access to `/dev/uhid`.

Keep the projector powered on. Its ADB serial is checked before commands are sent. The serial on its label may differ from `ro.serialno`.

## Setup

1. Clone the repository and copy the configuration template:

   ```sh
   git clone https://github.com/bodharma/wanbo-web-remote.git
   cd wanbo-web-remote
   cp .env.example .env
   ```

2. Set `PROJECTOR_HOST` in `.env` to your projector's address. The template address `192.0.2.10` is an example only. If the local port is in use, change `WEB_PORT`, for example to `8766`.

3. Download the ready-to-run public image:

   ```sh
   docker compose pull
   ```

4. Read the ADB serial without starting the web service:

   ```sh
   docker compose run --rm --no-deps --entrypoint sh remote -c \
     'adb connect "$PROJECTOR_HOST:$PROJECTOR_PORT" && adb -s "$PROJECTOR_HOST:$PROJECTOR_PORT" shell getprop ro.serialno'
   ```

   If the projector shows an ADB authorization prompt, authorize this computer and repeat the command. Set `PROJECTOR_SERIAL` in `.env` to the returned value.

5. Start the remote:

   ```sh
   docker compose up -d --no-build
   ```

   Open **http://127.0.0.1:8765**, or your configured `WEB_PORT`. Use `127.0.0.1` exactly: the Host check rejects other addresses.

Your `.env` contains local configuration and is excluded from Git and the Docker build context. Do not commit it.

## Prebuilt GHCR image

Image: `ghcr.io/bodharma/wanbo-web-remote:0.1.0`. The source and image are public. No GitHub account, token or registry login is required:

```sh
docker pull ghcr.io/bodharma/wanbo-web-remote:0.1.0
```

Published image platforms: `linux/arm64` and `linux/amd64`. To build from source instead, replace `docker compose pull` in setup with `docker compose build`. Both paths use the same `.env` configuration and startup command.

## Controls

| Action | Control |
| --- | --- |
| Navigate | Page buttons or arrows + Enter |
| Back / home | Buttons or Esc / Home / physical H key |
| Text | Select a field on the projector, type in the browser, then choose Paste |
| Mouse | Control mouse, then move, click and scroll; Esc releases capture |
| Screenshot | Refresh screen; the image does not update automatically |

Pasting replaces the projector clipboard; the computer clipboard is not read. A protocol acknowledgement does not guarantee that an app accepted the paste. Text is not written to logs or localStorage. The limit is 4096 UTF-8 bytes.

The mouse is a relative virtual HID device: watch its cursor on the projector. Clicking the screenshot does not send a click. Left/right click and vertical scrolling are supported; dragging is not implemented. Mouse capture requires browser Pointer Lock support.

Quick launch expects installed packages `org.jellyfin.androidtv`, `app.smarttube.fdroid`, and `org.xbmc.kodi`. The allowlist is `APPS` in `web_remote.py`. Launching does not force-stop an app or automatically play a video.

## Configuration

| Variable | Meaning |
| --- | --- |
| `PROJECTOR_HOST` | Required projector address |
| `PROJECTOR_PORT` | ADB TCP port; default `5555` |
| `PROJECTOR_SERIAL` | Required exact result of `getprop ro.serialno` |
| `WEB_PORT` | Local browser port; default `8765` |
| `IMAGE` | Compose image; default `ghcr.io/bodharma/wanbo-web-remote:0.1.0` |

Compose sets `WEB_ORIGIN` to match the published port. For custom `docker run` commands, set `WEB_ORIGIN` explicitly if you change that port. Inside the container HTTP listens on `0.0.0.0:8765`; Compose publishes only on the host's `127.0.0.1`.

## Operations

```sh
docker compose ps
docker compose logs --tail=50 remote
docker compose exec remote sh -c 'tail -n 50 /var/lib/wanbo-remote/device.log'
docker compose restart
docker compose down
```

Refresh the page after restarting: its page token changes. ADB keys and device logs live in Docker volumes. Normal `docker compose down` preserves them. The healthcheck tests HTTP without contacting the projector; `healthy` does not mean ADB is reachable.

For connection errors check the address, ADB port, authorization and `PROJECTOR_SERIAL`. After a DHCP address change, update `.env` and run `docker compose up -d --no-build`. For `No route to host` or timeouts, check Docker's LAN access and Wi-Fi client isolation. UHID permission failures appear in the device log; the remote does not grant new Android permissions.

On macOS check **System Settings → Privacy & Security → Local Network**: **OrbStack** and **OrbStack Helper** can have separate permissions. After a permission change, quit the OrbStack application completely and reopen it; restarting only the Docker VM may not apply the permission. This briefly interrupts its other containers.

## Access boundaries

There is no login for remote users. Keep the loopback-only publication; do not expose the port to a LAN or the internet without separate authentication and HTTPS. The API checks Host, Origin and a page token, and accepts only defined actions. The container runs without root, with a read-only filesystem, without the Docker socket, USB, privileged mode or host networking.

Android runs the official temporary scrcpy server with video, audio and automatic clipboard synchronization disabled. Wi-Fi, ADB settings and firmware are unchanged. Screenshots are captured privately and removed after transfer; a network failure during cleanup can leave a private temporary file until manually removed.

## Development

Use Python 3.14 and Node.js for browser checks:

```sh
python3 -m unittest discover -s tests
node tests/test_web_keyboard.cjs
```

Tests use a fictional address and identity, without a real projector. The Dockerfile pins the base image digest and checks the official scrcpy server 5.0.1 SHA-256. See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) for component sources and licenses.
