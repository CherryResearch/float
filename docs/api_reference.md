# Float Stack – OpenAI Responses API × MCP SDK Cheat‑Sheet
_A human- & machine-readable reference for the **Float** AI assistant._

> Dependencies are managed with Poetry. Run `poetry install` to set up all core and optional dependencies (see `pyproject.toml` and `poetry.lock`).
<small>Last updated 2025‑07‑21 (PDT)</small>

---
## 1 OpenAI Responses API (streaming‑first)
> Modern alternative to `chat/completions`; chunked streaming, deltas, and built‑in function‑call signals.
```bash
pip install openai
```
```python
import openai, time
openai.api_key = "<OPENAI_KEY>"

def stream_chat(messages):
    resp = openai.Responses.create(model="gpt-4o", stream=True, messages=messages)
    for chunk in resp:                        # iterable JSON payloads
        delta = chunk["choices"][0]["delta"]
        if "content" in delta:
            yield delta["content"]
```
Robust retry wrapper:
```python
from openai.error import RateLimitError, OpenAIError

def robust_stream(msgs, tries=3):
    for i in range(tries):
        try: yield from stream_chat(msgs); return
        except RateLimitError: time.sleep(2**i)
        except OpenAIError as e: raise RuntimeError(e)
```

---
## 2 Model Context Protocol (MCP) Python‑SDK
> Declarative agent protocol—manage *contexts*, stream events, and tool calls.
```bash
pip install mcp-client
```
```python
from mcp.client import MCPClient, Context
client = MCPClient()            # env: MCP_SERVER_URL / API_TOKEN
ctx = Context(system_prompt="You are Float AI.",
              tools=[{"name":"search","description":"web search",
                      "parameters":{"q":str}}])
ctx.add_message("user", "Weather in Paris?")
for ev in client.chat(ctx, stream=True):
    if ev.type == "message": print(ev.content,end="")
    elif ev.type == "tool_call":
        res = my_search(ev.parameters["q"])
        client.send_tool_result(ctx.id, ev.name, res)
```
`ev.type ∈ {message, tool_call, function_result}`.

---
## 3 Integration Skeleton (FloatService)
```python
class FloatService:
    def __init__(self, api_key, mcp_url, mode="api"):
        import openai
        from transformers import pipeline
        openai.api_key = api_key
        self.client = MCPClient(base_url=mcp_url)
        self.mode = mode                # 'api' | 'local' | 'dynamic'
        self.local_pipe = pipeline(
            "text-generation",
            model="mistralai/Mistral-7B-Instruct-v0.2",
            device_map="auto",
            max_new_tokens=256,
        )

    def _llm_stream(self, messages):
        if self.mode == "api":
            yield from robust_stream(messages)
        elif self.mode == "local":
            prompt = messages[0]["content"]
            for chunk in self.local_pipe(prompt, stream=True, return_full_text=False):
                yield chunk["generated_text"]
        else:
            raise NotImplementedError

    def chat(self, prompt):
        msgs = [{"role":"user","content":prompt}]
        yield from self._llm_stream(msgs)
```

---
## 4 Datastores & Embeddings
> **Weaviate** for vector search + **SQLite** for relational / graph tables.
```python
import weaviate
client = weaviate.connect_to_local()
emb = EmbeddingService("mixedbread-ai/bert-base-sentence")
vec = emb.embed("Paris weather")
client.data_object.create({"text": "Paris weather"}, "Memory", vector=vec)
```
Minimal extraction and embedding:
```python
from app.services import LangExtractService
from app.utils.embedding import EmbeddingService

lx = LangExtractService("Key facts", [])
items = lx.from_text("Alice met Bob in Paris.")
vec = EmbeddingService().embed_text(items[0]["text"])
```
Schema snapshot:
| Table | Purpose |
|---|---|
| `messages` | raw chat (id, role, content, ts, session) |
| `memory_vectors` | id, embedding (pgvector), json metadata |
| `graph_edges` | subject → predicate → object |

---
## 5 Tool Definition Schema
```python
{
  "name": "search",
  "description": "web search",
  "parameters": {"q": "string"},
  "requires_approval": false
}
```
*When MCP emits a* `tool_call`, *match by `name`, execute, then return with* `client.send_tool_result()`.

---
## 6 Tool Endpoints
Register a built‑in tool then invoke it:
```bash
curl -X POST /tools/register -d '{"name":"read_file"}'
curl -X POST /tools/invoke -d '{"name":"read_file","args":{"path":"notes.txt"}}'
```
Both endpoints return JSON results; `/tools/invoke` responds with `{"result": {"status": "invoked", "ok": true, "message": null, "data": "<tool output>"}}`.

---
## 7 Background Jobs (Celery)
```python
from tasks import celery_app

@celery_app.task
def long_search(query):
    return my_search(query)

# in tool handler
job = long_search.delay(ev.parameters["q"])
client.send_tool_result(ctx.id, ev.name, job.get())
```
Celery broker = Redis, results backend = PostgreSQL.

---
## 8 Operational Security
*   AES‑256‑GCM encrypts serialized memories at rest (`crypto.py`).
*   Keys stored in Google KMS; loaded at process start via Vault‑agent.
*   API keys masked before logging; PII redaction helper in `utils.sec`.

---
## 9 UI Integration Hooks
| Endpoint | Emits | Used in React UI |
|---|---|---|
| `/stream/responses` | SSE chunks from OpenAI | chat pane |
| `/stream/thoughts` | SSE of LLM thoughts & tool logs | agent console |
| `/agents/console` | JSON snapshot of active agents/tasks | agent console |
| `/events/mcp` | WebSocket of MCP events | tool sidebar |
All streams send `{event:"delta", data:"…"}` objects for progressive rendering.

---
## 10 Transformer Model API
| Endpoint | Purpose |
|---|---|
| `GET /api/transformers/models` | list available GPT-OSS/transformers models |
| `POST /api/transformers/generate` | generate text with a selected transformer model |
Example request:
```bash
curl -X POST /api/transformers/generate \
     -H 'Content-Type: application/json' \
     -d '{"model":"mistralai/Mistral-7B-Instruct-v0.2","prompt":"hi"}'
```

---
## 11  Reference Links
- OpenAI Responses announcement: <https://community.openai.com/t/introducing-the-responses-api/1140929>
- Responses API docs: <https://platform.openai.com/docs/api-reference/responses>
- MCP tutorial: <https://modelcontextprotocol.io/tutorials/building-mcp-with-llms>
- MCP Python SDK: <https://github.com/modelcontextprotocol/python-sdk>
- Example agents: <https://github.com/evalstate/fast-agent>, <https://github.com/Abiorh001/mcp_omni_connect>

---

## 12 Harmony Message Format
Float adopts the [Harmony envelope](https://github.com/openai/harmony) for structured
messages.  Each message's ``content`` is a list of typed parts.  Use the
`openai-harmony` utilities to render and parse Harmony tokens:

```python
from openai_harmony import (
    Conversation,
    HarmonyEncodingName,
    Message,
    Role,
    load_harmony_encoding,
)

# Build a conversation and render it to tokens
conv = Conversation(messages=[
    Message.from_role_and_content(Role.USER, "Ping?")
])
enc = load_harmony_encoding(HarmonyEncodingName.HARMONY_GPT_OSS)
request_tokens = enc.render_conversation_for_completion(conv, Role.ASSISTANT)

# ...send request_tokens to a model and obtain response_tokens...
response_tokens = enc.render(
    Message.from_role_and_content(Role.ASSISTANT, "Pong!")
)
msgs = enc.parse_messages_from_completion_tokens(response_tokens)
print(msgs[-1].content[0].text)
```

Serialized HTTP payloads look like:

```json
{
  "role": "user",
  "content": [{"type": "text", "text": "Ping?"}]
}
```

When calling ``LLMService.generate`` set ``response_format="harmony"`` to request
Harmony-formatted responses.

---

*Last updated 2025‑07‑21 (PDT).*&nbsp;
