import asyncio
import os
import uuid

from dotenv import load_dotenv

from core.engine import ZeroHitlEngine
from core.models import AgentSession
from core.skills import skill_manager


async def test_real_agent():
    print("[0-HITL] Starting real E2E test...")
    load_dotenv()

    if not os.getenv("OPENAI_API_KEY") and not os.getenv("ANTHROPIC_API_KEY"):
        print("Error: No API key found in .env (OPENAI_API_KEY or ANTHROPIC_API_KEY required)")
        return

    skill_manager.load_skills("./skills")

    engine = ZeroHitlEngine(model="gpt-4o")
    session = AgentSession(session_id=f"e2e-test-{uuid.uuid4().hex[:8]}")

    prompt = (
        "Use the available skills if needed. "
        "Create a python script 'check_system.py' that prints 'System OK: 0-HITL is alive', "
        "then run it using python."
    )

    print(f"User: {prompt}")

    response = await engine.chat(session, prompt)

    print(f"\nAgent Response: {response}")
    print("\nSession History Summary:")
    for msg in session.history:
        role = msg.role.value
        content = (msg.content[:100] + "...") if msg.content and len(msg.content) > 100 else msg.content
        tc = f" [ToolCalls: {len(msg.tool_calls)}]" if msg.tool_calls else ""
        print(f"- {role.upper()}: {content}{tc}")


if __name__ == "__main__":
    asyncio.run(test_real_agent())
