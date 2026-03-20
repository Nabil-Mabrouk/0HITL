import os
import yaml
import importlib.util
import sys
from pydantic import BaseModel
from typing import Dict, List, Optional
from core.tools import tool, registry

class SkillMetadata(BaseModel):
    name: str
    description: str
    version: str = "1.0.0"
    author: str = "0-HITL"
    required_permissions: List[str] = []
    docker_image: str = "python:3.12-slim"

class Skill:
    def __init__(self, folder_path: str):
        self.path = folder_path
        self.metadata = self._load_metadata()
        self.instructions = self._load_instructions()

    def _load_metadata(self) -> SkillMetadata:
        try:
            with open(os.path.join(self.path, "skill.yaml"), "r") as f:
                return SkillMetadata(**yaml.safe_load(f))
        except FileNotFoundError:
            return SkillMetadata(name=os.path.basename(self.path), description="Custom skill without metadata")

    def _load_instructions(self) -> str:
        try:
            with open(os.path.join(self.path, "SKILL.md"), "r", encoding="utf-8") as f:
                return f.read()
        except FileNotFoundError:
            return "No specific instructions provided for this skill."

class SkillManager:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(SkillManager, cls).__new__(cls)
            cls._instance.skills = {}
            cls._instance.active_skills = set()
        return cls._instance

    def load_skills(self, directory: str = "./skills"):
        if not os.path.exists(directory): 
            os.makedirs(directory)
            return
        for folder in os.listdir(directory):
            path = os.path.join(directory, folder)
            if os.path.isdir(path) and "skill.yaml" in os.listdir(path):
                skill = Skill(path)
                self.skills[skill.metadata.name] = skill

    def get_catalog(self) -> str:
        """Returns a compact list of skills for the LLM."""
        catalog = "AVAILABLE SKILLS (Call 'activate_skill' to use them):\n"
        for name, s in self.skills.items():
            catalog += f"- {name}: {s.metadata.description}\n"
        return catalog

    async def activate_skill_tools(self, skill_name: str):
        """Dynamically imports tools from the skill and registers them with a prefix."""
        if skill_name not in self.skills:
            return "Skill not found."
            
        skill = self.skills[skill_name]
        tools_path = os.path.join(skill.path, "tools.py")
        
        if os.path.exists(tools_path):
            try:
                spec = importlib.util.spec_from_file_location(f"{skill_name}.tools", tools_path)
                module = importlib.util.module_from_spec(spec)
                sys.modules[f"{skill_name}.tools"] = module
                spec.loader.exec_module(module)
                return f"Tools of skill '{skill_name}' successfully loaded."
            except Exception as e:
                return f"Failed to load tools of skill '{skill_name}': {e}"
        return "No additional tools found for this skill."

skill_manager = SkillManager()

@tool
async def activate_skill(skill_name: str):
    """
    Loads a specific skill into your current context.
    Use this as soon as you identify a mission requires expertise you lack.
    """
    if skill_name not in skill_manager.skills:
        return f"Error: Skill '{skill_name}' does not exist in the catalog."

    skill = skill_manager.skills[skill_name]
    skill_manager.active_skills.add(skill_name)
    
    await skill_manager.activate_skill_tools(skill_name)

    return (
        f"--- SKILL ACTIVATED : {skill_name} ---\n"
        f"ADDITIONAL INSTRUCTIONS:\n{skill.instructions}\n"
        f"GRANTED PERMISSIONS: {skill.metadata.required_permissions}\n"
        f"You can now use the files and scripts present in this skill's folder."
    )
