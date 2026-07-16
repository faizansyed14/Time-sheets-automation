QWEN (VLLM) INTEGRATION — SHORT GUIDE

What we use Qwen for
- Vision extraction (Extract Email / file extraction / Upload / chat-upload): Qwen via vLLM
- Leave flags + review summaries: deterministic code (`validation.py`) — not an LLM
- Accuracy: similar to GPT for our timesheet extraction use-case
- Speed: slightly slower than GPT (depends on your vLLM host)

What we do NOT switch (yet)
Agentic Chat stays on GPT because it needs OpenAI-style tool calling (function calls).
Vision extraction does not need tool calling.

App config (our side)
Set in root `.env`:
  VISION_PROVIDER=vllm
  AI_PROVIDER=openai

  VLLM_BASE_URL=https://<your-vllm-host>
  VLLM_API_KEY=<optional>
  VLLM_MODEL=qwen3-vl-32b

  EXTRACTION_MODEL=qwen3-vl-32b
  OPENAI_VISION_MODEL=gpt-4o   (only used if VISION_PROVIDER=openai)

  VLLM_MAX_IMAGES_PER_PROMPT=4   (common server limit; prevents failures/fallbacks)

If vLLM TLS is self-signed:
  Preferred: VLLM_CA_BUNDLE=certs/<root-ca>.crt + VLLM_TLS_VERIFY=true
  Temp only: VLLM_TLS_VERIFY=false

Enable Qwen for Agentic Chat (server-side)
This must be done on the vLLM SERVER startup (not in our app).
How to Enable Agentic Chat on Qwen (Server Side)

--enable-auto-tool-choice
--tool-call-parser hermes
Then switch chat to Qwen:
AI_PROVIDER=vllm
AGENT_CHAT_MODEL=<your-qwen-model>
Fallback: If you can't relaunch the server, you could build a manual JSON-parsing shim in code — but it's more brittle. Recommended: just add the flags.
The One-Liner Problem
Your vLLM server returns this error on tool calls because the flags aren't set:
"auto" tool choice requires --enable-auto-tool-choice and --tool-call-parser to be set
Fix: add the two flags above. Zero code changes needed.
