"""entrypoint — uses systemd socket fd if available, else binds loopback (local dev)"""
import os
import uvicorn

if int(os.environ.get("LISTEN_FDS", 0)) == 1:
    uvicorn.run("backend.main:app", fd=3)
else:
    # loopback only — production deployments must front this with a reverse proxy
    port = int(os.environ.get("PORT", 8080))
    uvicorn.run("backend.main:app", host="127.0.0.1", port=port)
