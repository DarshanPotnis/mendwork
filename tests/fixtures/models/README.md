# Model provider fixtures

Recorded HTTP bodies the provider contract tests replay with respx. No test calls a provider.

| File | Provenance |
|---|---|
| `ollama_chat_ok.json` | Captured unchanged from a local Ollama 0.33.3 server running `qwen3:4b-instruct-2507-q4_K_M`, answering the canonical `click_three_candidates` request (2026-09-13). The reply holds no prompt text. |
| `gemini_generate_ok.json` | Written from Gemini's documented `generateContent` response shape. No key was available, so it was not captured. |
| `openai_chat_ok.json` | Written from the documented Chat Completions response shape. No key was available, so it was not captured. |

A fixture written from documentation proves the adapter reads the documented shape, not that a
live provider still sends it; `make live-providers` checks the configured provider against the real
service.
