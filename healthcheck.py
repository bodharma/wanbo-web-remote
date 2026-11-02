import urllib.request

import remote

request = urllib.request.Request(
    f"http://127.0.0.1:{remote.PORT}/healthz",
    headers={"Host": remote.origin.netloc},
)
urllib.request.urlopen(request, timeout=3).close()
