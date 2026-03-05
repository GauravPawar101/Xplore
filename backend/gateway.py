"""
EzDocs API Gateway — launcher.

Run from backend/: python gateway.py
Delegates to gateway.app (see gateway/app.py).
"""

import uvicorn

from shared.config import HOST, PORT, WS_MAX_SIZE

if __name__ == "__main__":
    uvicorn.run(
        "gateway.app:app",
        host=HOST,
        port=PORT,
        reload=False,
        log_level="info",
        ws_max_size=WS_MAX_SIZE,
    )
