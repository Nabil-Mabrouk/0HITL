import re
from enum import Enum
from pydantic import BaseModel

class RiskLevel(Enum):
    SAFE = 0
    SUSPICIOUS = 1
    DANGEROUS = 2
    BLOCKED = 3

class SafetyVerdict(BaseModel):
    level: RiskLevel
    reason: str
    suggestion: str = ""

class SuperEgo:
    def __init__(self):
        # Immediate danger patterns
        self.blacklist = [
            r"rm\s+-rf\s+/",            # Root destruction attempt
            r"chmod\s+777",             # Permissive permissions
            r"curl.*\|\s*bash",         # Blind download and execution
            r"/etc/shadow",             # Password access
            r"nc\s+-e",                 # Reverse shell (Netcat)
            r"base64\s+--decode",       # Malicious code masking attempt
            r"> \/dev\/sda"             # Direct write to physical disk
        ]

    def analyze_command(self, tool_name: str, arguments: dict) -> SafetyVerdict:
        """Semantic and heuristic analysis of tool arguments."""
        
        # 1. BASH specific analysis
        if tool_name == "execute_bash":
            cmd = arguments.get("command", "").lower()
            
            # Blacklist check
            for pattern in self.blacklist:
                if re.search(pattern, cmd):
                    return SafetyVerdict(
                        level=RiskLevel.BLOCKED,
                        reason=f"Critical command detected: {pattern}",
                        suggestion="Use more specific commands and avoid root accesses."
                    )

            # Suspicion detection (Network commands to private IPs)
            if re.search(r"192\.168\.|10\.|172\.", cmd):
                return SafetyVerdict(
                    level=RiskLevel.SUSPICIOUS,
                    reason="Attempt to access local network detected.",
                    suggestion="Explain why you need to access the local network."
                )

        # 2. File writing analysis
        if tool_name == "write_file" or tool_name == "write_and_test_code":
            path = arguments.get("filename", arguments.get("path", "")).lower()
            if path.startswith(("/etc", "/var", "/root")) or ".." in path:
                 return SafetyVerdict(
                    level=RiskLevel.BLOCKED,
                    reason="Attempt to write outside of workspace (/app).",
                    suggestion="Write only in the current directory."
                )

        return SafetyVerdict(level=RiskLevel.SAFE, reason="Everything seems fine.")

superego = SuperEgo()
