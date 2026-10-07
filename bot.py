"""Discord bot entry point. Feature registrations live in focused modules."""
from bot_app.core import *  # noqa: F401,F403

# Importing these modules registers their commands on the shared bot instance.
from bot_app.moderation_commands import *  # noqa: F401,F403,E402
from bot_app.message_commands import *  # noqa: F401,F403,E402
from bot_app.role_commands import *  # noqa: F401,F403,E402
from bot_app.events import *  # noqa: F401,F403,E402

if __name__ == "__main__":
    token = os.getenv("DISCORD_TOKEN", "").strip()
    if not token:
        print("[ERROR] DISCORD_TOKEN not found in .env")
        raise SystemExit(1)
    bot.run(token)
