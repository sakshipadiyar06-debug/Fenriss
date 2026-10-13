import os

import uvicorn

if __name__ == "__main__":
    # Locally: 127.0.0.1:8000. On a host, set HOST=0.0.0.0 (the platform provides PORT).
    uvicorn.run("app.main:app", host=os.getenv("HOST", "127.0.0.1"), port=int(os.getenv("PORT", "8000")),
                proxy_headers=True, forwarded_allow_ips="*")
