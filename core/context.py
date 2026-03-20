import tiktoken
from typing import List
from core.models import Message, Role

class ContextManager:
    def __init__(self, model: str = "gpt-4o", max_tokens: int = 4000):
        self.model = model
        self.max_tokens = max_tokens
        try:
            self.encoding = tiktoken.encoding_for_model(self.model)
        except KeyError:
            self.encoding = tiktoken.get_encoding("cl100k_base") # Fallback

    def count_tokens(self, messages: List[Message]) -> int:
        """Calculates token count in history."""
        num_tokens = 0
        for msg in messages:
            if msg.content:
                num_tokens += len(self.encoding.encode(msg.content))
            if msg.tool_calls:
                for tc in msg.tool_calls:
                    num_tokens += len(self.encoding.encode(tc.function))
                    num_tokens += len(self.encoding.encode(tc.arguments))
        return num_tokens

    async def compact_if_needed(self, messages: List[Message], engine) -> List[Message]:
        """Triggers compression if window is saturated."""
        current_tokens = self.count_tokens(messages)
        
        if current_tokens < self.max_tokens * 0.8:
            return messages

        print(f"📉 [0-HITL] Context saturated ({current_tokens} tokens). Launching Compaction...")
        
        system_prompt = [m for m in messages if m.role == Role.SYSTEM]
        to_summarize = [m for m in messages if m.role != Role.SYSTEM]

        summary_prompt = "Summarize the current state of the mission, modified files, and next steps. Be very technical and concise."
        
        response_stream = await engine.call_llm(
            messages=to_summarize + [Message(role=Role.USER, content=summary_prompt)],
            use_tools=False
        )

        full_summary = await engine.collect_completion_text(response_stream)

        new_history = system_prompt + [
            Message(
                role=Role.SYSTEM, 
                content=f"--- COMPACTED STATE OF THE PAST ---\n{full_summary}\n-----------------------------"
            )
        ]
        
        return new_history
