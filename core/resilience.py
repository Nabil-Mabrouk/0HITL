from datetime import datetime

from core.models import Message, Role


class ErrorInterpreter:
    def __init__(self):
        self.common_fixes = {
            "ModuleNotFoundError": "The Python library '{pkg}' is missing. Use 'execute_bash' to run 'pip install {pkg}' or check requirements.txt.",
            "PermissionError": "You don't have permission to access this file. Try working in the /app directory.",
            "FileNotFoundError": "The file does not exist. Use 'ls' or 'execute_bash' to list files before retrying.",
            "JSONDecodeError": "The data format is corrupted. Check JSON structure.",
            "docker.errors.APIError": "Docker container execution failed. Ensure paths and permissions are correct.",
        }

    def analyze(self, error_msg: str) -> str:
        """Analyzes the error and proposes a proactive solution."""
        for error_type, fix_template in self.common_fixes.items():
            if error_type in error_msg:
                pkg = "the missing module"
                if "No module named" in error_msg:
                    pkg = error_msg.split("'")[1] if "'" in error_msg else pkg
                return f"0-HITL ANALYSIS: {fix_template.format(pkg=pkg)}"

        return f"TECHNICAL FAILURE: {error_msg}. Analyze the cause and try an alternative approach."


class CognitiveResilience:
    def __init__(self, memory, engine):
        self.memory = memory
        self.engine = engine
        self.failure_counter = {}

    async def analyze_and_learn(self, error_msg: str, tool_call_context: str) -> str:
        error_id = hash(error_msg + tool_call_context)
        self.failure_counter[error_id] = self.failure_counter.get(error_id, 0) + 1

        if self.failure_counter[error_id] > 3:
            return "CRITICAL ALERT: Repeated failures. Abandon this approach and try a radically different strategy."

        past_fixes = await self.memory.search_related(f"Solution pour l'erreur : {error_msg}")
        if past_fixes:
            return f"MEMORY RECALL: Similar error solved before. Suggested solution: {past_fixes[0]}"

        print("[0-HITL] Analyzing new error...")
        diagnostic_prompt = f"""
        TECHNICAL ANALYSIS REQUIRED:
        Error: {error_msg}
        Action attempted: {tool_call_context}

        Explain the probable technical cause and suggest a remediation command (e.g., installation, path change).
        Be brief and purely technical.
        """

        diagnosis = await self.engine.call_llm(
            [Message(role=Role.SYSTEM, content=diagnostic_prompt)],
            use_tools=False,
        )

        diagnosis_text = (await self.engine.collect_completion_text(diagnosis)).strip()
        if not diagnosis_text:
            diagnosis_text = "No diagnosis produced. Inspect the last tool output and try a different approach."

        return f"NEW DIAGNOSIS: {diagnosis_text}"

    async def register_success(self, error_msg: str, solution_action: str):
        archive_content = f"FIX CONFIRMED for '{error_msg}' -> Action: {solution_action}"
        await self.memory.archive_message(
            content=archive_content,
            metadata={"type": "resilience_fix", "timestamp": str(datetime.now())},
        )
        print("[0-HITL] New solution learned and archived.")
