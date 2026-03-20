from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from core.engine import ZeroHitlEngine
from core.models import AgentSession
from core.bus import event_bus
from core.runner import runner
import uuid
import os

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

engine = ZeroHitlEngine()
sessions = {}

class ChatReq(BaseModel):
    user_input: str
    session_id: str = None

@app.post("/chat")
async def chat(req: ChatReq):
    sid = req.session_id or str(uuid.uuid4())
    if sid not in sessions: sessions[sid] = AgentSession(session_id=sid)
    resp = await engine.chat(sessions[sid], req.user_input)
    return {"session_id": sid, "response": resp}

@app.websocket("/ws/mission-control/{sid}")
async def websocket_endpoint(websocket: WebSocket, sid: str):
    await websocket.accept() 
    await event_bus.subscribe(sid, websocket)
    try:
        while True: await websocket.receive_text()
    except WebSocketDisconnect: print(f"Client disconnected from session {sid}")


@app.get("/session-files/{sid}/{file_path:path}")
async def get_session_file(sid: str, file_path: str):
    session_root = os.path.abspath(runner.get_session_root(sid))
    target_path = os.path.abspath(os.path.join(session_root, file_path))

    try:
        inside_session = os.path.commonpath([session_root, target_path]) == session_root
    except ValueError:
        inside_session = False

    if not inside_session:
        raise HTTPException(status_code=403, detail="Forbidden path")
    if not os.path.exists(target_path) or not os.path.isfile(target_path):
        raise HTTPException(status_code=404, detail="File not found")

    return FileResponse(target_path)

static_dir = os.path.join(os.path.dirname(__file__), "static")
if os.path.exists(static_dir):
    app.mount("/dashboard", StaticFiles(directory=static_dir, html=True), name="static")
