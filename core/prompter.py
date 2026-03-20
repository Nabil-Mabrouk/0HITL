import os

class ProfileManager:
    def __init__(self, profiles_dir: str = "./profiles"):
        self.profiles_dir = os.path.abspath(profiles_dir)
        os.makedirs(self.profiles_dir, exist_ok=True)

    def get_profile(self, name: str, variables: dict) -> str:
        """Loads an .md file and injects variables (mission, tools, etc.)."""
        path = os.path.join(self.profiles_dir, f"{name}.md")
        if not os.path.exists(path):
            return f"You are an expert agent named {name}. Always think step by step."
            
        with open(path, "r", encoding="utf-8") as f:
            template = f.read()
            
        # Dynamic injection (e.g. {{mission}})
        for key, value in variables.items():
            template = template.replace(f"{{{{{key}}}}}", str(value))
        return template
