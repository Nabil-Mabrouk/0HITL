import uvicorn
from gateway.api import app
from core.skills import skill_manager
import os
from dotenv import load_dotenv

def startup():
    print("🔋 [0-HITL] Initializing system...")
    
    # Load environment variables
    load_dotenv()
    
    # 1. Load JIT skills
    skill_manager.load_skills("./skills")
    
    # 2. Check workspace
    workspace = os.getenv("HOST_WORKSPACE_PATH", "./workspace")
    if not os.path.exists(workspace):
        os.makedirs(workspace, exist_ok=True)
    
    print("🚀 [0-HITL] Daemon operational. Awaiting missions.")

# Attach startup event to FastAPI
app.add_event_handler("startup", startup)

if __name__ == "__main__":
    uvicorn.run("gateway.api:app", host="0.0.0.0", port=8000, 
                reload=True,
                reload_dirs=["core", "gateway", "skills", "profiles"], 
                reload_excludes=["workspace/*", "*.pyc", "__pycache__/*"])  
