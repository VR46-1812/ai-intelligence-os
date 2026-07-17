# Local Setup, Models, and First Run

## 1. Recommended development environment

Use Windows Codex with the project folder located in WSL2 or use Codex directly against the Windows folder if the toolchain is installed there. Do not maintain two active copies of the repository.

Recommended local components:

- Git.
- WSL2 Ubuntu 24.04 or a supported Ubuntu distribution.
- Python 3.12.
- `uv` for Python dependencies and virtual environment.
- Node.js current LTS and npm.
- Ollama for local models.
- Current NVIDIA Windows driver with WSL GPU support if using Ollama inside WSL.

Docker Desktop is not required for phase one.

## 2. WSL resource cap

On Windows, create `%UserProfile%\.wslconfig`:

```ini
[wsl2]
memory=7GB
processors=6
swap=4GB
localhostForwarding=true

[experimental]
autoMemoryReclaim=gradual
sparseVhd=true
```

Then run in PowerShell:

```powershell
wsl --shutdown
```

If the 8B model repeatedly runs out of host RAM, benchmark Ollama natively on Windows before increasing WSL beyond 7 GB. Preserve Windows usability.

## 3. Model acquisition policy

Only download models from the official developer organization or a traceable official runtime registry. Record:

- Original model organization and repository.
- Exact model/tag and digest.
- License.
- Quantization.
- Context configuration.
- Download date.
- Runtime version.

Never assume “available for download” means OSI open source. Phase-one model profiles must record the actual license and intended use.

## 4. Starting model profiles

### Scout

Purpose: classification, routing, metadata cleanup, and compact relevance estimates.

Start with:

```powershell
ollama pull qwen3:4b
```

Expected role: fast and economical; it is not the final deep-analysis authority.

### Analyst/reviewer

Purpose: evidence-grounded paper analysis, code analysis, skeptical review, and synthesis.

Start with:

```powershell
ollama pull qwen3:8b
```

The 8B Q4-class package is roughly 5 GB before runtime/context overhead. Use one request at a time, moderate context, and benchmark VRAM. If it does not operate reliably within the laptop budget, use the 4B model for the first working release and retain the 8B profile as optional.

### Embeddings

Start with one small local embedding model, benchmarked before adoption:

```powershell
ollama pull embeddinggemma
```

Alternative candidates may be tested, but phase one adopts only one. Keyword/FTS search must continue to work without embeddings.

### Official sources

- Qwen organization/model cards: `https://huggingface.co/Qwen`
- Qwen repository: `https://github.com/QwenLM`
- Ollama model library: `https://ollama.com/library`
- Ollama downloads: `https://ollama.com/download`

Before implementation, verify the exact runtime tag, digest, and license again because registries change.

## 5. Runtime controls

Use model profiles rather than scattered model strings:

```yaml
profiles:
  scout:
    runtime: ollama
    model: qwen3:4b
    max_context_tokens: 8192
    max_output_tokens: 1200
    temperature: 0.1
    keep_alive: 0
  analyst:
    runtime: ollama
    model: qwen3:8b
    max_context_tokens: 12288
    max_output_tokens: 3000
    temperature: 0.1
    keep_alive: 0
  embed:
    runtime: ollama
    model: embeddinggemma
    batch_size: 8
```

These are starting limits, not guaranteed maxima. Benchmark them on the actual runtime and lower context before changing hardware/resource limits.

## 6. Building from scratch with Codex

### Step 1 — Create the repository

Create/open a folder named `ai-intelligence-os`. Copy this specification package into `docs/spec/`, and place the supplied `AGENTS.md` at the repository root.

Start Codex in the repository and first ask it to summarize active instructions and identify M0.1. Do not ask it to implement the entire PRD.

### Step 2 — Scaffold only

Use Codex Prompt 01 from `prompts/CODEX_EXECUTION_PROMPTS.md`. Verify backend health test and frontend build before proceeding.

### Step 3 — Establish persistence

Use the prompts for configuration, quality tooling, SQLite migration, repositories, and identity service.

### Step 4 — Produce the first useful vertical slice

The first valuable UI slice should be:

`arXiv fixture -> normalized SQLite records -> GET /items -> Today/Explore UI`

This provides a professional, real interface before local LLM analysis is added.

### Step 5 — Add local intelligence

After deterministic ingestion is stable:

- Verify Ollama health.
- Run the scout model against fixed fixtures.
- Validate JSON.
- Add ranking.
- Add fast briefs.
- Add deep dives last.

## 7. Initial UI specification

### App shell

- Left navigation: Today, Explore, Topics, Opportunities, System.
- Header: date, global search, pipeline state, local model state.
- Main workspace: maximum readable width with dense responsive grids.

### Today screen

- Compact pipeline funnel.
- “What materially changed” hero card.
- Ranked technical developments.
- Ranked commercial opportunities.
- Deep dives with confidence/status.
- Connector and model warnings.

### Deep-dive screen

- Left section navigation.
- Central report.
- Right evidence drawer.
- Tabs for Method, Evaluation, Code, Reproduction, Business, Learning.

### Visual language

- Background: near-black/navy neutral, not pure black.
- Surfaces: layered graphite with subtle borders.
- Primary accent: restrained cyan.
- Secondary accent: indigo.
- Warning: amber; critical: red; verified: green.
- Typography: Inter for UI; JetBrains Mono for identifiers/code.
- Avoid glassmorphism overload, animated gradients, fake charts, and generic chatbot bubbles.

## 8. Expected local run commands after M0

Backend:

```bash
cd backend
uv sync
uv run uvicorn app.main:app --reload --port 8000
```

Frontend:

```bash
cd frontend
npm install
npm run dev
```

Ollama health:

```powershell
ollama list
curl http://localhost:11434/api/tags
```

Expected URLs:

- UI: `http://localhost:5173`
- API documentation: `http://localhost:8000/docs`
- API health: `http://localhost:8000/health`

## 9. Benchmark before daily scheduling

Record for scout, analyst, and embeddings:

- Cold-load time.
- Tokens/second.
- Peak VRAM.
- Peak system RAM.
- Maximum reliable context.
- Schema-valid response rate.
- Time to analyze one representative paper.

Adopt the 8B analyst only if Windows remains usable and the full pipeline stays within the declared budget.

## 10. Zero-cost boundary

Do not enter an OpenAI, Anthropic, Gemini, Groq, hosted Hugging Face, Tavily, Exa, Pinecone, or cloud credential into the application. Codex is used as the development agent under the user's existing access; the product itself uses local inference and free public sources.
