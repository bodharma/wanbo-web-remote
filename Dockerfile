# syntax=docker/dockerfile:1
FROM python:3.14-slim@sha256:930e1abba7c4e8f96ed93e8012c35d174d7732752731bbd5e8e757d7ec6f3966

LABEL org.opencontainers.image.title="Wanbo Web Remote" \
      org.opencontainers.image.source="https://github.com/bodharma/wanbo-web-remote" \
      org.opencontainers.image.licenses="Apache-2.0"

RUN apt-get update \
    && apt-get install -y --no-install-recommends adb ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --uid 10001 --create-home remote \
    && mkdir -p /home/remote/.android /var/lib/wanbo-remote \
    && chown -R remote:remote /home/remote /var/lib/wanbo-remote

WORKDIR /opt/wanbo
ADD --chmod=0444 --checksum=sha256:764eb6f79811d5211fe9df341120882ba9994c7a61b897d7bf3fb662e53bc536 \
    https://github.com/Genymobile/scrcpy/releases/download/v5.0.1/scrcpy-server-v5.0.1 \
    /opt/wanbo/scrcpy-server
COPY remote.py web_remote.py web_remote.html healthcheck.py LICENSE THIRD_PARTY_NOTICES.md ./

ENV BIND_HOST=0.0.0.0 \
    HOME=/home/remote \
    STATE_DIR=/var/lib/wanbo-remote \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
USER 10001:10001
EXPOSE 8765
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 CMD ["python", "healthcheck.py"]
ENTRYPOINT ["python", "web_remote.py"]
