"""entrypoint — uses systemd socket fd if available, else binds PORT (local dev)"""
import os
import uvicorn

if int(os.environ.get("LISTEN_FDS", 0)) == 1:
    uvicorn.run("backend.main:app", fd=3)
else:
    port = int(os.environ.get("PORT", 8080))
    uvicorn.run("backend.main:app", host="0.0.0.0", port=port)
