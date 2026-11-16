---
title: Qwen Studio 24/7 OpenAI API Server
emoji: 🤖
colorFrom: indigo
colorTo: purple
sdk: docker
app_port: 7860
pinned: false
license: mit
---

# 🚀 Qwen Studio - 24/7 Cloud OpenAI-Compatible API Server

A high-performance, 100% free OpenAI-compatible API server and web UI for Alibaba's **Qwen** flagship models (`qwen4-max`, `qwen3.8-max`, `qwen3.7-plus`, `qwen3.8-omni-flash`, `qwen3.7-max`, `qwen3.5-omni-plus`).

Features:
- 🌐 **OpenAI Compatible v1 API**: Drop-in replacement for OpenAI API endpoints (`/v1/chat/completions`, `/v1/models`).
- ⚡ **Full SSE Streaming & JSON Support**: Works seamlessly with Python `openai` SDK, NextChat, Open WebUI, Cursor, LangChain.
- 📊 **Real-time Token & API Dashboard**: Live metrics for total requests, prompt & completion tokens, latency, and request logs.
- 💬 **Authentic Qwen Studio Web UI**: High-end modern chat interface with 9 integrated tools, multi-modal attachments, and code artifact sandboxes.
- ☁️ **100% Free 24/7 Cloud Deployment**: Ready for Hugging Face Spaces & Render with zero credit card required.

---

## 🛠️ Endpoints

| Endpoint | Method | Description |
| :--- | :--- | :--- |
| `/v1/models` | `GET` | List all available authentic Qwen models |
| `/v1/chat/completions` | `POST` | OpenAI-compatible chat completion (Streaming & Non-streaming) |
| `/dashboard` | `GET` | Live token analytics and server metrics |
| `/api/stats` | `GET` | JSON metrics for token counts and logs |
| `/` | `GET` | Qwen Studio Web Chat UI |

---

## 💻 Quickstart (Python SDK)

```python
from openai import OpenAI

# Connect to your 24/7 Cloud Server
client = OpenAI(
    base_url="https://<YOUR-SPACE-OR-RENDER-URL>/v1",
    api_key="sk-qwen-local"
)

# Stream response from flagship Qwen4-Max
stream = client.chat.completions.create(
    model="qwen4-max",
    messages=[{"role": "user", "content": "Explain quantum computing in 2 lines."}],
    stream=True
)

for chunk in stream:
    if chunk.choices and chunk.choices[0].delta.content:
        print(chunk.choices[0].delta.content, end="", flush=True)
print()
```

---

## 📡 Quickstart (cURL)

```bash
curl -N -X POST "https://<YOUR-SPACE-OR-RENDER-URL>/v1/chat/completions" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer sk-qwen-local" \
  -d '{
    "model": "qwen4-max",
    "messages": [{"role": "user", "content": "Hello Qwen!"}],
    "stream": true
  }'
```

---

## 🚀 1-Click Cloud Deployment

### Deploy to Hugging Face Spaces:
1. Go to [huggingface.co/new-space](https://huggingface.co/new-space).
2. Set Space Name: `qwen-studio-api`.
3. Select **Docker** (Blank).
4. Connect this GitHub repository or push directly.
5. Done! Your server will be live 24/7 at `https://<username>-qwen-studio-api.hf.space`.

### Deploy to Render:
1. Go to [render.com](https://render.com) and click **New → Web Service**.
2. Connect your GitHub repository.
3. Select **Docker** runtime.
4. Click **Deploy Web Service** (Free tier).
5. Done! Your server will be live 24/7 at `https://qwen-studio-api.onrender.com`.

---

## 📄 License
MIT License. Free for personal and commercial usage.
