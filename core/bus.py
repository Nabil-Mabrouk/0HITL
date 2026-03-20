import asyncio
from typing import Dict, List, Any

class EventBus:
    def __init__(self):
        self.connections: Dict[str, List[Any]] = {}

    async def subscribe(self, session_id: str, websocket: Any):
        if session_id not in self.connections:
            self.connections[session_id] = []
        self.connections[session_id].append(websocket)

    async def broadcast(self, session_id: str, event_type: str, data: dict):
        if session_id in self.connections:
            payload = {"type": event_type, "data": data}
            for ws in list(self.connections[session_id]):
                try:
                    await ws.send_json(payload)
                except Exception:
                    self.connections[session_id].remove(ws)

event_bus = EventBus()