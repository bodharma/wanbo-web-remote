# Third-party components

The image includes the unmodified Android server from [scrcpy 5.0.1](https://github.com/Genymobile/scrcpy/releases/tag/v5.0.1), copyright Genymobile and contributors, under the Apache License 2.0. The license text is included in `LICENSE`.

The server is downloaded from the official release while building the image, rather than committed to this repository. Its SHA-256 is `764eb6f79811d5211fe9df341120882ba9994c7a61b897d7bf3fb662e53bc536`.

The control packet format and USB HID mouse descriptor follow scrcpy 5.0.1. Relevant upstream sources: [`control_msg.c`](https://github.com/Genymobile/scrcpy/blob/v5.0.1/app/src/control_msg.c), [`hid_mouse.c`](https://github.com/Genymobile/scrcpy/blob/v5.0.1/app/src/hid/hid_mouse.c), and [`Controller.java`](https://github.com/Genymobile/scrcpy/blob/v5.0.1/server/src/main/java/com/genymobile/scrcpy/control/Controller.java).

Debian's `adb` and its dependencies are installed by the operating system package manager. Their licenses and copyright notices remain available under `/usr/share/doc` in the image. The Python base image carries its own bundled notices.
