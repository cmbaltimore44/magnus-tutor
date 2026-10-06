# Magnus Tutor: Build Plan (v2)

## Goal

Build a free, local, Jarvis-style study tutor that lives alongside Magnus (my existing terminal companion, see its README). It knows who I am, what courses I'm taking, and what's in my notes and textbooks, so I can:

1. Ask it random questions about my courses and get answers grounded in my materials.
2. Work through problem sets with a Socratic "professor in office hours" style (never a solver by default), including complex math, physics, and code.
3. Have it read my handwritten GoodNotes notes and my textbook PDFs and use them as context.
4. Add new courses each semester and edit the tutor's behavior (prompts) whenever I want.

It has its own polished web interface (the primary UI) and is a **separate project and web app from Life Tracker**. Magnus is the front door: it launches sessions, runs the focus timer, and the timer is always visible inside the tutor app.

Magnus is a Node/Ink app (Supabase backend, macOS Keychain session, `~/.config/magnus/` for local state, journal scripts on `$PATH`, Whisper dictation via `~/bin/dictate`). The tutor must fit that world rather than replace it.

## Core requirements (from me)

- Free to run: **Ollama is the default LLM provider.** No paid API required for any feature.
- Accepts **textbook PDFs** per course, in addition to GoodNotes exports.
- **New courses can be added any time** (new semesters), and old ones archived.
- **The office hours prompt is editable at any time**, without touching code.
- A **nice, purpose-built interface**, not just a chat box.
- Must handle **complex math, physics, and code** problems well.
- A **separate web app from Life Tracker**: its own project, port, and storage.
- **Must not degrade my computer.** Near-zero footprint when idle, bounded memory/CPU/disk when active, nothing installed system-wide, and nothing that runs in the background unless I started it. See Resource budget and system safety.
- **Start a focus timer from Magnus in the terminal** and see it clearly in the tutor web app at all times, with a single shared timer (never two).

## Key decisions

### 1. LLM provider: Ollama by default, behind an interface

Build an `LLMProvider` interface. Implementations:

- **Ollama (default).** Local and free.
- **Anthropic API (optional, off by default).** Only used if I add a key and explicitly turn it on, e.g. as an "escalate this problem to a stronger model" button or for hard handwriting. Never called unless enabled. Show a clear indicator in the UI whenever data leaves my machine.

**Model roles, not one model.** Different jobs need different models. Config maps roles to models, and the same model can fill several roles on a small machine:

```yaml
models:
  tutor:     # conversation, Socratic dialogue, explanations
  solver:    # hidden reasoning pass that solves problems privately (see below)
  coder:     # code generation/review/debugging
  vision:    # handwritten notes and photos of work
  embedding: # retrieval
```

First-run **hardware check**: detect Apple Silicon chip and RAM, then recommend model sizes. Rough guide for 4-bit quantized models on unified memory: ~0.6 GB per billion parameters plus overhead, so 16 GB comfortably runs ~7-8B, 32 GB runs ~14B (32B is tight), 64 GB+ runs 32B well. Claude Code should check the current Ollama library for the best available reasoning, coding, and vision models for that hardware (families like Qwen, DeepSeek-R1 distills, Llama, Gemma are the usual candidates) and **benchmark candidates on my own eval set** (see Testing) rather than assuming. Ollama keeps one large model loaded at a time, so role switching costs load time; offer a "single model for everything" preset for low-RAM machines.

**Honest expectation:** local models are weaker than frontier cloud models at hard multi-step math and physics. The architecture below compensates with tools and verification rather than relying on the model's raw ability.

### 2. Solving hard problems while staying Socratic: the hidden solver pass

The tutor must be able to solve problems in order to guide me well, but must not hand over solutions. So:

1. When I submit a problem, the engine immediately starts a **background solver pass** using the `solver` model (reasoning mode enabled). I don't see it.
2. The solver produces a structured reference solution: final answer, steps, key concepts, common mistakes.
3. **Verification before trust:**
   - **Math:** check with SymPy (symbolic equality, derivatives/integrals, solving equations, matrix ops). Fall back to numeric checks (NumPy/SciPy) with random test values.
   - **Physics:** unit and dimensional checks with `pint`, plus a numeric sanity check (limiting cases, order of magnitude).
   - **Code:** run it (see code sandbox) against tests.
   - **Self-consistency:** run the solver multiple times (e.g. 3) when time allows and compare final answers. Disagreement lowers confidence.
4. The reference solution gets a **confidence level** (verified / agreed / uncertain). It is stored on the problem record but never shown unless I explicitly unlock the full solution.
5. The tutor uses it to choose hints, spot where my attempt diverges, and check my answer. If confidence is low, the tutor must say so ("I'm not fully sure about this one, let's check this step together") and rely on verification tools instead of pretending certainty.
6. The solver pass runs **while I'm still working on my own attempt**, so the latency is hidden. Show a subtle "preparing" indicator.

Alternative valid approaches from me should be accepted. Don't mark me wrong just because my method differs from the reference; check my result and reasoning independently with the tools.

### 3. PyTorch: not in v1

Add PyTorch only once there's real data (knowledge tracing over my attempt history, Whisper fine-tuning on `~/.local/share/dictate/corpus`, a misconception classifier). Log the `attempts` data cleanly from day one so it's ready.

### 4. Interface: its own web app, plus Magnus as a launcher

- **Primary UI: a polished local web app** served by the backend on localhost, opened with `magnus tutor` (or a standalone command). Optionally wrap it as a desktop app later (Tauri). It must not feel like a generic chat template. Make deliberate design choices and apply a real design pass.
- **Magnus:** palette actions, quick "ask" in the terminal, focus-timer integration, and a key to open the current session in the web app.
- Both talk to the same backend, so a session can move between them.
- **Separate from Life Tracker.** Different repo, different server, different data. See Project structure and boundaries.

### 5. Course materials: GoodNotes exports and textbooks

**GoodNotes** has no public API, so ingestion is export-based: I export notebooks as PDFs into a watched folder per course (ideally iCloud-synced).

1. Detect new or changed PDFs (by content hash).
2. Rasterize pages (PyMuPDF) and extract embedded text where present.
3. For pages without usable text, transcribe with the `vision` model into Markdown with LaTeX for math. Cache per page by hash; never re-transcribe unchanged pages.
4. Keep the page image reference with every chunk, since handwriting transcription will contain errors and I need to check against the original.

**Textbook PDFs** are a different problem (hundreds of pages, structured, equation-heavy):

1. Per-course `textbooks/` folder. Ingest in the background with a visible progress bar and resumability, since a big book takes a while.
2. Use the PDF's bookmarks/table of contents to build a chapter and section tree. Chunk by section, with the heading path stored ("Ch. 5 > 5.3 > Gauss's Law").
3. **Page mapping:** store both PDF page index and printed page number (they often differ), so citations match the physical book.
4. Math-heavy PDFs extract badly with plain text extraction. Evaluate a layout/math-aware converter (e.g. Marker or similar open-source tools) against PyMuPDF on a couple of my actual textbooks and pick the better one. Handle scanned books with OCR as a fallback.
5. **Problem lookup:** detect numbered exercises so I can say "chapter 5, problem 12" and have the tutor retrieve the exact problem text.
6. Scope retrieval by course by default, with an option to search across all courses, including archived ones ("didn't I cover this in linear algebra last year?").
7. Citations must point to source and page ("Textbook ch. 5.3, p. 142" or "Lecture 4 notes, p. 12"), and the UI should be able to show that page.

### 6. Code problems

- **Editor** in the web UI (Monaco or CodeMirror) with syntax highlighting and a Run button.
- **Execution sandbox:** run code in a temp directory with a timeout, memory limit, and no network by default. Use a container (Docker/Podman) when available; otherwise a restricted subprocess. Any code the *model* wrote must be shown to me before it runs.
- **Languages:** Python first, then whatever my courses need (ask me). Support running provided test cases and showing results.
- **Tutor behavior for code mirrors office hours:** ask for my attempt first, then guide with hints (read the failing test, add a print, check the boundary case) before giving fixes. Debugging help should teach how to find the bug, not just patch it.
- Use the `coder` model for code turns, and use real execution results as ground truth rather than the model's guess about what code does.

## Project structure and boundaries

Three separate projects:

```
magnus/          existing Node/Ink terminal app (small additions only)
life-tracker/    existing web app, untouched
magnus-tutor/    NEW: Python backend + web frontend, its own repo, port, storage, and design
```

- The tutor is **not part of Life Tracker**: no shared code, deployment, auth, or database tables, and no changes to the Supabase schema. It has its own local SQLite database and config directory.
- Its only touchpoint with the rest of my setup is Magnus, through the narrow focus-timer interface below (plus optional palette actions and quick-add tasks later).
- Changes to Magnus are limited to: `magnus timer` subcommands, a watcher for timer state, and the Tutor section and palette actions. Don't refactor unrelated Magnus code, and keep its existing tests passing.
- Launching: `magnus tutor` (or a standalone `tutor` command) starts the backend if needed and opens the web app. The backend serves the built frontend, so it's one process.
- **Location: a sibling directory, not inside the Magnus repo** (e.g. `~/Projects/magnus-tutor/` next to `~/Projects/magnus/`). Magnus is Node and the tutor is Python with a separate frontend, so they need separate dependencies, builds, tests, and version history. Keeping the tutor out also keeps Magnus lean.
- **Working across the two repos:** do almost all work in `magnus-tutor/`. The few Magnus changes (timer subcommands, state watcher, Tutor palette actions) are done in a separate branch of the Magnus repo, in their own commits, against the timer contract documented in `magnus-tutor/docs/timer-contract.md`, so Magnus stays releasable at every step.

## Shared focus timer

Requirement: I start and control a focus timer from Magnus in the terminal, and I can always see it in the tutor web app, with exactly one timer running.

1. **One timer, owned by Magnus.** Step one is reading Magnus's source (`src/`) to find how the timer is persisted today (the README says it survives quitting Magnus). Reuse that as the single source of truth. Do not build a second timer in the tutor. Document its schema as a small versioned JSON file.
2. **Timestamp-based state.** Store phase (focus, break, long break, waiting), `phase_started_at`, `phase_duration`, pause info, round count, label, and task id. Every client computes remaining time from timestamps, so there's no per-second syncing, and laptop sleep behaves the same as it does in Magnus today. Writes are atomic (temp file then rename) and carry a version number.
3. **Control surface: `magnus timer` subcommands.** `status [--json]`, `start [--task ID | --label "..."]`, `pause`, `resume`, `skip`, `add5`, `switch`, `stop`, `discard`. They use the same code path as the TUI keys (`t` and `T`), including logging to `focus_sessions` when a focus round ends, and work whether or not the TUI is running. When the TUI is running it watches the state file and updates its status bar within about a second.
4. **Tutor bridge.** The tutor backend watches the state file and pushes changes to the browser (SSE or WebSocket). Commands from the web app go through `magnus timer ...` (or the shared module). **The tutor never writes `focus_sessions` and never talks to Supabase**, so minutes can't be logged twice.
5. **How it shows in the web app:**
   - A persistent **timer pill in the global header on every screen**: phase, mm:ss, label, round dots. Click to expand controls: pause/resume, skip, +5, stop, and "switch to this problem".
   - **Browser tab title updates live** (e.g. `▶ 14:32 · PHYS 201`), so it's visible even when the tab is in the background.
   - The Workspace shifts to a calmer look during breaks.
   - Optional pop-out mini timer (Document Picture-in-Picture where the browser supports it).
   - "Start focus" in the Workspace pre-labels the round with course and problem set. If a timer is already running, offer "switch to this", which keeps the clock and round count, matching Magnus's existing switch-task behavior.
6. **Starting from Magnus.** The existing `T` flow keeps working unchanged. A new palette action, "Tutor: start problem-set session", starts a focus round labeled `office hours: <course> <pset>` and opens the web app on that session. A tutor session started while a timer is already running links to it by label and time range.
7. **Phase-end alerts without double ringing.** Setting `alerts: auto | terminal | web | both`. In `auto`, the terminal rings if the TUI is alive (heartbeat in the state file), otherwise the web app does (in-browser chime plus the Notification API). The web app always shows an in-page banner.
8. **Insights.** Focus minutes flow through Magnus's existing logging with the course label, so they appear in Magnus Insights by label. The tutor shows per-course focus minutes from the same labels and time ranges.
9. **Tests and edge cases.** TUI not running; tutor backend not running; two clients issuing commands at once (version check, last write wins); sleep and clock changes; missing or corrupted state file; offline. Add tests proving `magnus timer` behaves identically to the TUI keys, and that existing timer behavior hasn't regressed.

## Architecture

```
Web app (primary)  ──┐
                     ├──>  magnus-tutor backend (Python, FastAPI, localhost only)
Magnus (Node/Ink) ───┘            │
                                  ├── LLMProvider (Ollama | Anthropic, optional)
                                  ├── Prompt manager (editable files, templating)
                                  ├── Tutor engine (modes, hint ladder, gates)
                                  ├── Solver pipeline (hidden pass + verification)
                                  ├── Tools (SymPy, NumPy/SciPy, pint, code sandbox)
                                  ├── Ingestion (notes, textbooks, syllabus)
                                  ├── Retrieval (embeddings + SQLite)
                                  ├── Session log (SQLite)
                                  └── Focus timer bridge (watches/controls Magnus's timer)
```

- Backend: Python 3.11+, FastAPI, SQLite (sqlite-vec or FAISS for vectors), PyMuPDF. Stream responses (SSE).
- Embeddings via a local model (sentence-transformers or Ollama), behind an interface.
- Frontend: React + Vite + TypeScript, KaTeX for math, Monaco/CodeMirror for code, pdf.js for showing source pages.
- Config in `~/.config/magnus-tutor/`. Optional API keys in the macOS Keychain, the same way Magnus stores its session.
- Bind to localhost only.

## Data model

Plain files for things I edit by hand, SQLite for generated data.

**`profile.yaml`**: about me.

```yaml
name: ...
school: ...
program: ...
year: ...
background: ...        # strengths, weak areas, prior courses
learning_preferences:  # e.g. likes intuition before formalism
  - ...
```

**`courses/<slug>/course.yaml`**: one folder per course.

```yaml
code: ...
title: ...
instructor: ...
term: ...              # e.g. Fall 2026
status: active         # active | archived
description: ...
topics: [ ... ]
notation_conventions: ...
folders:
  notes: ~/Documents/Magnus/notes/<slug>/
  textbooks: ~/Documents/Magnus/textbooks/<slug>/
  other: ~/Documents/Magnus/other/<slug>/   # syllabus, slides, problem sets
languages: [ ... ]     # for code courses
prompt_overrides: {}   # optional per-course prompt files
```

**SQLite (generated):** `documents`, `chunks` (document, section path, page, printed page, text, embedding, image_ref), `problems` (course, source, text, reference_solution, confidence), `sessions` (course, mode, start/end, linked focus label and time range), `messages`, `attempts` (session, problem, concept tags, hint level reached, solved, used full solution, misconception note), `prompt_versions`.

## Course management

- **"New course" wizard in the UI:** name, code, term, instructor, then drop in the syllabus. The tutor reads it, proposes topics, schedule, grading info, and notation conventions, and I confirm or edit. Then point it at (or drop in) notes and textbook PDFs.
- Course files are plain YAML, so I can also create one by hand. A `tutor course add` CLI command and a Magnus palette action do the same thing.
- **New semester flow:** archive the previous term's courses in one action, then add the new ones. Archived courses are hidden from the default view but stay searchable and keep their history.
- Adding material later (a new textbook, another notebook) just re-triggers incremental ingestion for that course.

## Prompts: always editable

All prompts live as plain Markdown files, not in code:

```
~/.config/magnus-tutor/prompts/
  persona.md          # shared tone and ground rules
  office_hours.md     # the Socratic problem-set prompt
  ask.md              # general Q&A
  quiz.md             # quiz mode
  solver.md           # internal hidden solver pass
~/.config/magnus-tutor/courses/<slug>/prompts/   # optional per-course overrides
```

- Prompts support variables the engine fills in: `{{student_profile}}`, `{{course}}`, `{{retrieved_context}}`, `{{problem}}`, `{{hint_level}}`, `{{attempt_status}}`, etc. Document the full list in the UI.
- **Prompt editor page in the web app:** edit with a live preview of the fully rendered prompt for a sample problem, save, reset to default, and see version history (every save stored in `prompt_versions`, with restore). Also editable directly as files or via `$EDITOR`.
- **Changes take effect on the next turn** (hot reload), no restart.
- Per-course overrides win over global ones.
- A **"test this prompt"** button runs a few scripted scenarios (stuck student, wrong answer, asks for the solution) and shows the transcript, so I can see what an edit changed.
- Ship this as the default `office_hours.md`:

```
I am working on {{course}}. Act like a professor in office hours,
not a solver. For every problem I bring you: ask me for my attempt before
responding. If I'm stuck, ask a guiding question rather than giving the next
step. Give hints in small increments, smallest one first. Only give a full
worked solution if I explicitly ask for one, and even then, ask if I want it
before or after one more attempt. When I get an answer, check it and explain
the reasoning, don't just confirm.
```

**Engine gates are separate from the prompt and configurable.** Local models follow instructions less reliably, so the engine enforces these in code and passes state into the prompt. Each can be toggled in Settings (all on by default), so editing the prompt never fights hidden behavior:

- **Attempt gate:** first reply to a new problem asks for my attempt.
- **Hint ladder:** 0 nothing, 1 which concept applies, 2 which method or first move, 3 the specific next step, 4 full solution. Advance at most one level per stuck turn. Log the level reached.
- **Full-solution gate:** only unlocked by my explicit request, then ask "before or after one more attempt?"
- **Output check:** before sending a reply, a lightweight check that it doesn't leak the reference solution when it shouldn't (e.g. final answer or key steps appearing at hint level < 4). If it does, regenerate with a stricter instruction.

## Tutor modes

1. **Office hours (problem sets):** the Socratic mode above.
2. **Ask me anything:** normal Q&A grounded in my notes and textbooks with citations. Still explains at my level and can offer to turn a topic into a quick quiz.
3. **Quiz me (later):** generates questions from my materials, grades against sources.

**Grounding rules:**
- Prefer my notes and textbooks over general knowledge, and cite them.
- If my notes, the textbook, and the model disagree, say so rather than silently choosing.
- If the answer isn't in my materials, say that, answer from general knowledge, and label it as such.
- Use my course's notation conventions when writing math.

## Interface (web app)

Design goals: calm, focused, keyboard-first, beautiful typography (math must look excellent), light and dark following macOS. Consider mapping to Magnus's theme families (Heather, Lakeglow, Beacon, Hearth) so the two feel related. Do an intentional design pass; don't ship a default component-library look.

Screens:

- **Global header (every screen):** the focus timer pill, current course, and quick access to ask a question.
- **Home:** my courses this term, recent sessions, what to review, material ingestion status.
- **Course page:** materials (notes, textbooks) with ingestion status, topics, session history, later a mastery view.
- **Workspace (the main screen):** split layout.
  - Left: the problem (rendered with KaTeX, with image upload/paste for screenshots and photos of my handwritten work) and the chat.
  - Right, tabbed: **Scratchpad** (my work and attempt photos), **Code** (editor, run, test results), **Sources** (the cited textbook or notes page, shown as the actual page image with the passage highlighted).
  - A visible hint-level indicator and an explicit "Show me the solution" action, so unlocking it is deliberate.
- **Library:** all documents, ingestion progress, re-ingest, and a way to fix bad transcriptions of handwritten pages.
- **Prompts:** the editor described above.
- **Settings:** model roles, hardware check and model pulling, gate toggles, optional cloud provider, appearance.

## Magnus integration

Follow existing conventions (palette actions, footer key hints, `a` quick add, offline-friendly).

- New **Tutor** section plus palette actions: "Tutor: ask a question", "Tutor: start problem-set session", "Tutor: open web app", "Tutor: ingest notes", "Tutor: new course".
- Terminal Ask view with streaming output (math as plain text). A key opens the same session in the web app.
- **Focus timer:** see the Shared focus timer section. Magnus owns the timer, the web app displays and controls it through `magnus timer`, and minutes are logged once by Magnus.
- **Follow-up tasks:** unresolved or weak concepts offered as tasks through quick add (`review: gauss's law !low +2`).
- **Journal:** optional end-of-session note via the journal scripts.
- **Voice (later):** reuse `~/bin/dictate`.
- No new Supabase table in v1. Tutor state is local SQLite.

## Milestones

Build in order and stop after each so I can try it. Each has an acceptance check.

1. **Backend skeleton + Ollama.** FastAPI app, `LLMProvider` with Ollama, hardware check and model recommendations, profile and course YAML loading, a CLI chat with context injected. *Check:* chat locally and the answer reflects my course details. Record idle and peak RAM/CPU, confirm the model unloads after the keep-alive period, and run `tutor doctor` after `tutor stop` to show nothing is left running.
2. **Prompt system + office hours engine.** Prompt files with variables and hot reload, per-course overrides, sessions, attempt gate, hint ladder, full-solution gate, `attempts` logging. *Check:* paste a problem and it asks for my attempt; "I'm stuck" yields only the smallest hint; editing `office_hours.md` changes behavior on the next turn. Automated scripted-conversation tests for the gates.
3. **Solver pipeline + tools.** Hidden background solver pass, SymPy/NumPy/pint verification, self-consistency, confidence levels, answer-leak output check. Build the benchmark set and model comparison script here. *Check:* on my benchmark set, report accuracy per model and per role, and pick defaults from the data. Also record latency (time to first token, tokens/sec, total reply time) and tutor latency while the solver pass is running.
4. **Web app foundation.** Design system, Home, course list, Workspace with streaming chat, KaTeX, image paste/upload, hint indicator. *Check:* work a physics problem end to end in the browser, including a photo of my handwritten attempt.
5. **Shared focus timer.** Read how Magnus persists its timer, document the state schema, add `magnus timer` subcommands and the state watcher in Magnus (with tests), then build the tutor bridge, header pill, live tab title, controls, and alert setting. *Check:* start a timer with `T` in Magnus and it appears in the web app within about a second; pause from the web app and the Magnus status bar updates; quit Magnus and the web app still shows the correct remaining time; the focus minutes appear exactly once in Insights.

6. **Ingestion: notes + textbooks.** Watched folders, GoodNotes PDF handling with vision transcription fallback and caching, textbook ingestion with chapter tree, page mapping, problem lookup, progress UI, citations, Sources tab. *Check:* ask a question only answerable from a handwritten page and one only answerable from a textbook section; both cite the right page.
7. **Code workspace.** Editor, sandboxed run, tests, `coder` model, Socratic debugging behavior. *Check:* a buggy program gets guided toward the fix with hints, not a patch, and runs are sandboxed.
8. **Course management + prompt editor UI.** New-course wizard with syllabus parsing, archive flow for new semesters, prompt editor with preview, history, and test button. *Check:* add a new course from a syllabus PDF in under five minutes, and edit then restore a prompt from the UI.
9. **Magnus integration.** Tutor section, palette actions, terminal Ask, focus-timer link, open-in-web-app. *Check:* full Ask flow inside Magnus.
10. **Optional cloud provider.** Anthropic implementation behind a toggle, "escalate to stronger model" button, clear data-leaves-machine indicator.
11. **Quiz mode + mastery tracking.** Question generation from sources, grading, per-concept mastery from `attempts`.
12. **Later, with real data:** PyTorch knowledge tracing, Whisper fine-tuning, voice mode.

## Target hardware and performance budget

**My machine: MacBook Pro, M5 Pro, 24 GB unified memory (307 GB/s memory bandwidth).** Treat this as the design target, and keep the hardware check so the app adapts if this ever changes.

**Default preset: Standard, built around one 14B-class model.**
- A single ~14B-class, 4-bit quantized model fills the **tutor** and **solver** roles. Tutor turns run with reasoning/thinking **off** for speed. The solver pass runs with reasoning **on**, with a capped thinking budget (e.g. ~4-6k tokens).
- Memory budget: loaded model weights plus context cache stay under roughly 12 GB, leaving 10+ GB for macOS, the browser, GoodNotes, and Magnus.
- **Coder role:** try the main model first. Only add a separate coder model (~7-8B class) if the benchmark shows a clear gain, and load it on demand, never alongside the main model.
- **Vision role:** loaded **only during ingestion**, never at the same time as the main chat model. If I start a tutor session during an ingestion run, pause ingestion, unload the vision model, and resume afterward.
- **Embedding model:** small (under ~1 GB) so it can stay resident alongside the main model.
- A 32B-class model is **not** a default. Offer it as an opt-in "experimental" setting with a warning about memory pressure and slower replies. Also evaluate mixture-of-experts models if they fit under the memory budget, since they generate faster.

**Latency targets (to be measured, adjusted if unrealistic):**
- Retrieval: under 200 ms.
- Warm tutor reply: first token within ~2 s, full typical reply (~200 words) within ~10 s, streamed.
- Cold start (model not loaded): under ~10 s extra on the first message after an unload.
- Hidden solver pass: up to ~2 minutes is acceptable because it runs in the background while I work on my attempt.
- Handwritten page transcription: tens of seconds per page is acceptable, one time only, cached.

**Concurrency problem to solve explicitly:** with one loaded model, a long solver pass could make a tutor reply wait in line. Tutor turns must always take priority.
- Claude Code should first measure how Ollama handles two concurrent requests to the same model on this machine (and what it costs in memory and speed), and how tutor latency changes while the solver runs.
- If tutor latency exceeds the targets, use this policy: the solver runs only while I'm idle, **pauses or cancels the moment I send a message**, and resumes after the reply.
- Never let the solver pass delay a tutor reply.

**Report real numbers:** tokens/sec, time to first token, and total reply time for each candidate model on this Mac, saved with the benchmark results.

## Resource budget and system safety

Principle: the tutor should be invisible to my computer when I'm not using it, and stay within a known budget when I am. The heaviest thing in this project is the local LLM, so most of this section is about controlling it.

**When idle (no session running):**
- No model stays in RAM. Set a short Ollama `keep_alive` (default ~2-5 minutes, configurable) and provide an "Unload models now" button.
- No polling. File watchers use OS file events, not loops. No background GPU work.
- The backend only runs when I launch it (`magnus tutor`), and shuts itself down after a configurable idle period. `tutor stop` stops it and unloads models immediately.
- **Nothing starts at login.** No launch agents or daemons. (Check Ollama's own app setting and turn off start-at-login if I don't want it.)

**Memory:**
- The hardware check sets a RAM budget and only recommends models whose weights fit comfortably, leaving headroom for macOS, the browser, GoodNotes, and Magnus itself. As a rule, don't recommend models using more than roughly half of total RAM.
- **One model loaded at a time** (limit loaded models and parallel requests to 1). Cap the context window (default 4-8k tokens), since memory use grows with context length.
- Pre-flight check before loading a model: if free memory is low, warn me and suggest a smaller model instead of letting the system swap.
- Resource presets: **Light** (one small model for every role, hidden solver pass runs only on demand, no self-consistency), **Standard**, **Full** (separate solver and coder models, self-consistency on). The hardware check picks the default preset.

**CPU/GPU and battery:**
- The hidden solver pass runs sequentially, never alongside another model on low-RAM machines.
- Ingestion is the other heavy job. Run it as a **low-priority, single-worker, pausable, resumable** background task with small batches. Large vision-transcription jobs (many handwritten pages) are queued and need my confirmation with a page count and rough time estimate.
- Setting to run heavy background jobs **only when plugged in** (default on).
- A global **"Pause all background work"** switch in the UI.

**Disk:**
- Models are multi-GB each. Never pull a model without showing its size and asking. Settings shows disk use per model, plus a clean-up view.
- Textbooks and notes are **read in place** by default, not duplicated. Only derived data (extracted text, embeddings, cached page images) is stored, in a cache directory with a size cap and a clear-cache button.

**Safe installation:**
- Python dependencies live in a project-local virtual environment (e.g. `uv` or `venv`). No global installs, no changes to system settings or shell config beyond the `magnus tutor` command.

**Sandbox limits:** code I or the model runs is capped on CPU time, memory, process count, and wall-clock time, so a runaway loop can't hog the machine.

**Visibility:** a small resource indicator in Settings (loaded model, RAM in use, background jobs running), so I can always see what the tutor is costing.

**Prove nothing is left running:** after `tutor stop` (and after quitting Magnus), verify with `ps` and Activity Monitor that no tutor backend or Ollama model-runner processes remain, memory has returned to its baseline, and no login items, launch agents, or cron jobs were added. Include this as a repeatable script (`tutor doctor`) that reports running processes, loaded models, and disk use. If Ollama's app was installed, tell me how to turn off its start-at-login option.

**Measure it:** record idle and peak RAM/CPU in the README for my Mac with my usual apps open (browser, GoodNotes, Magnus). Idle should show no model memory in use. If peak use during a session causes memory pressure, the default preset should be lowered.

## Testing and evaluation

- **Benchmark set:** ~30 problems with known answers, spread across my actual math, physics, and code courses, used to compare models per role and to catch regressions when I change models or prompts. This is how the default models get chosen.
- Unit tests for context assembly, hint-ladder transitions, prompt rendering, and ingestion caching.
- Scripted conversation tests for the behavioral gates, including one that tries to trick the tutor into revealing the answer.
- A retrieval eval set (~30 questions about my own notes and textbooks with expected source pages).
- Sandbox tests: infinite loops, memory blowups, and file/network access attempts must be contained.

## Non-goals (for now)

- No custom model training in v1.
- No syncing tutor state through Supabase.
- No automatic scraping of GoodNotes or any cloud service. Export-based only.
- No reading companion or concept-map features yet.
- Single user, localhost only.
- Not merged into Life Tracker: no shared code, deployment, or database with it.

## Privacy and cost

- Everything stays on my machine by default. The only exception is the optional cloud provider, which is off unless I enable it and always flagged in the UI.
- Cache transcriptions, embeddings, and reference solutions; never reprocess unchanged material.
- Textbook and notes files are read in place by default, not duplicated. Only derived data (text, embeddings, page image cache) is stored locally. Nothing is uploaded anywhere.

## Questions for Claude Code to ask me before starting

1. Hardware is already known: MacBook Pro M5 Pro, 24 GB unified memory. Confirm it with the hardware check and apply the Target hardware and performance budget section.
2. Where is the Magnus repo? (Already decided: the tutor lives in its own sibling repo, `magnus-tutor/`, not inside Magnus. Magnus only gets the small changes listed in Project structure and boundaries.)
3. What are my courses this term, and which are math-heavy, physics-heavy, or code-heavy? Which programming languages?
4. Which textbooks do I have as PDFs, and are they text-based or scanned?
5. Where do my GoodNotes exports land today, and can I sync them to a watched folder?
6. Should the web app be a browser tab, or do I want it wrapped as a desktop app?
7. When a focus phase ends, should the terminal, the web app, or both alert me? (Default: `auto`.)
