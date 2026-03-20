import inspect
import json
from typing import Callable, Any, Dict, List

class ToolRegistry:
    def __init__(self):
        self.tools: Dict[str, Callable] = {}
        self.schemas: List[Dict[str, Any]] = []

    def register(self, func: Callable):
        name = func.__name__
        if name in self.tools:
            print(f"⚠️ [Tools] Warning: Overwriting tool '{name}'")

        sig = inspect.signature(func)
        doc = func.__doc__ or "No description."
        
        schema = {
            "type": "function",
            "function": {
                "name": name,
                "description": doc.strip(),
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "required": []
                }
            }
        }

        for p_name, p in sig.parameters.items():
            p_type = "string"
            if p.annotation == int: p_type = "integer"
            elif p.annotation == bool: p_type = "boolean"
            elif p.annotation == float: p_type = "number"
            
            schema["function"]["parameters"]["properties"][p_name] = {
                "type": p_type,
                "description": p_name
            }
            if p.default == inspect.Parameter.empty:
                schema["function"]["parameters"]["required"].append(p_name)

        self.tools[name] = func
        self.schemas.append(schema)
        return func

registry = ToolRegistry()

def tool(func: Callable):
    return registry.register(func)