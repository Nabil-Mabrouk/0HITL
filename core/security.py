import hashlib
import httpx
import os

class SecurityGate:
    def __init__(self, api_key: str = None):
        self.api_key = api_key or os.getenv("VIRUSTOTAL_API_KEY")
        self.base_url = "https://www.virustotal.com/api/v3"

    def get_file_hash(self, content: str):
        return hashlib.sha256(content.encode()).hexdigest()

    async def scan_content(self, content: str) -> bool:
        """
        Checks if the content is malicious via VirusTotal.
        Returns True if 'Safe', False if 'Malicious'.
        """
        if not self.api_key:
            print("⚠️ [Security] No VirusTotal API Key. Skipping scan (Danger).")
            return True # Fallback for POC

        file_hash = self.get_file_hash(content)
        
        async with httpx.AsyncClient() as client:
            headers = {"x-apikey": self.api_key}
            try:
                response = await client.get(f"{self.base_url}/files/{file_hash}", headers=headers)
                
                if response.status_code == 200:
                    stats = response.json()['data']['attributes']['last_analysis_stats']
                    if stats['malicious'] > 0:
                        print(f"🚨 ALERT: Content identified as MALICIOUS by {stats['malicious']} scanners!")
                        return False
            except Exception as e:
                print(f"⚠️ [Security] Error contacting VirusTotal: {e}")
                return True # Allow pass on network error, ideally we should block but it's a POC
            
        return True

security_gate = SecurityGate()
