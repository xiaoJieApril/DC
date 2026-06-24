import os

import uvicorn

from dashboard_api import app, start_bot_process


def env_port():
    for name in ("SERVER_PORT", "PORT", "P_SERVER_PORT"):
        value = os.getenv(name, "").strip()
        if value.isdigit():
            return int(value)
    return 30335


def auto_start_bot():
    value = os.getenv("ORIHOST_AUTO_START_BOT", "1").strip().lower()
    return value not in ("0", "false", "off", "no")


if __name__ == "__main__":
    host = os.getenv("HOST", "0.0.0.0")
    port = env_port()

    if auto_start_bot():
        try:
            start_bot_process()
            print("[Orihost] Discord bot started.")
        except Exception as exc:
            print(f"[Orihost] Dashboard is starting, but bot did not auto-start: {exc}")

    print(f"[Orihost] Dashboard listening on {host}:{port}")
    uvicorn.run(app, host=host, port=port)
