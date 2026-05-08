# test_connection.py — run this first to confirm reachability
import requests, os
from dotenv import load_dotenv
load_dotenv()

host = os.getenv("OLLAMA_HOST", "http://localhost:11434")

try:
    r = requests.get(f"{host}/api/tags", timeout=5)
    models = [m["name"] for m in r.json().get("models", [])]
    print(f"✓ Connected to Ollama at {host}")
    print(f"  Available models: {models}")
except Exception as e:
    print(f"✗ Cannot reach Ollama: {e}")
    print(f"  Check: is OLLAMA_HOST set correctly? Is port 11434 open on that machine?")