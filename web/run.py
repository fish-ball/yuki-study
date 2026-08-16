"""启动管理台：uvicorn web.backend.main:app"""
from __future__ import annotations

import uvicorn


def main() -> None:
    uvicorn.run(
        "web.backend.main:app",
        host="127.0.0.1",
        port=8787,
        reload=False,
    )


if __name__ == "__main__":
    main()
