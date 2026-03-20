import os

import uvicorn
from dotenv import load_dotenv

load_dotenv()

from core.skills import skill_manager
from gateway.api import app, engine, telegram_connector


async def startup():
    print("[0-HITL] Initializing system...")

    skill_manager.load_skills("./skills")

    workspace = os.path.abspath("./workspace")
    if not os.path.exists(workspace):
        os.makedirs(workspace, exist_ok=True)

    os.makedirs(os.path.join(workspace, "system"), exist_ok=True)

    print(f"[0-HITL] Agent model: {engine.model}")
    print(f"[0-HITL] Memory model: {engine.memory_model}")
    await telegram_connector.start()
    print("[0-HITL] Daemon operational. Awaiting missions.")


async def shutdown():
    await telegram_connector.stop()


app.add_event_handler("startup", startup)
app.add_event_handler("shutdown", shutdown)


if __name__ == "__main__":
    uvicorn.run(
        "gateway.api:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        reload_dirs=["core", "gateway", "skills", "profiles"],
        reload_excludes=["workspace/*", "*.pyc", "__pycache__/*"],
    )
