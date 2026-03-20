from core.models import AgentSession, Message, Role
from core.engine import ZeroHitlEngine

# Central registry for active agents to allow Orchestrator tracking
active_agents = {}

class SubAgent:
    def __init__(self, agent_id: str, mission: str, parent_session: AgentSession):
        self.agent_id = agent_id
        self.mission = mission
        self.parent_session = parent_session
        self.engine = ZeroHitlEngine()
        
        self.session = AgentSession(
            session_id=f"sub_{agent_id}",
            history=[
                Message(role=Role.SYSTEM, content=f"You are an expert assigned for: {mission}. Respond concisely.")
            ]
        )

    async def run(self, task_details: str) -> str:
        """Executes the mission and returns the final report to the parent."""
        print(f"🤖 [0-HITL] Sub-Agent '{self.agent_id}' starts its mission...")
        # Actually triggering the child chat
        result = await self.engine.chat(self.session, task_details)
        print(f"✅ [0-HITL] Sub-Agent '{self.agent_id}' has finished.")
        return result
