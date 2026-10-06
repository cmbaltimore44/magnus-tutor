# Magnus Tutor

A free, local, Socratic study tutor for your own courses. It knows what you're
taking and what's in your notes and textbooks, and it acts like a professor in
office hours, not an answer machine. Models run on your Mac through
[Ollama](https://ollama.com); nothing leaves the machine unless you turn on the
optional cloud model and escalate a specific reply.

- **Office hours** for problem sets: asks for your attempt first, hints one
  level at a time, and checks your answer with real math (SymPy, units, code
  runs). A hidden solver prepares a verified reference solution in the
  background while you work.
- **Ask** anything about a course, grounded in your notes and textbooks with
  page citations you can click to see the actual page.
- **Quiz** from your own materials, graded against the source page; mastery is
  tracked per concept.
- **Code** workspace with a sandboxed Run (Python, SML, Java, Ruby) and
  Dockerfile/Kubernetes checks; debugging help teaches how to find the bug.
- **Writing coach** for writing courses: questions about your thesis and
  sources, never ghostwriting.
- **Focus timer** (optional) shared with Magnus, a terminal companion app.

## Requirements

- **macOS on Apple Silicon.** The code sandbox uses `sandbox-exec`, scanned
  pages use macOS Vision OCR, and the optional API key lives in the Keychain.
- **16 GB of RAM or more** is recommended for the default models; there's a
  Light preset for smaller machines (see [Models and resources](#models-and-resources)).
- [Ollama](https://ollama.com), [uv](https://docs.astral.sh/uv/), Node.js (to build the web app).
- Python 3.12 (uv installs it for you if needed).
- Optional, for the code workspace: the toolchains for the languages you
  want to run (e.g. SML/NJ, a JDK, Ruby).

## Quick start

```sh
git clone https://github.com/cmbaltimore44/magnus-tutor.git
cd magnus-tutor
uv sync                                  # exact versions from uv.lock
(cd web && npm install && npm run build) # once, and after UI changes
.venv/bin/tutor init                     # config folders, default prompts, hardware check
.venv/bin/tutor models pull …            # pull the recommended models (shows sizes, asks first)
.venv/bin/tutor start                    # starts the backend (+ Ollama if needed), opens the web app
```

```sh
.venv/bin/tutor stop       # stops everything and unloads models
.venv/bin/tutor doctor     # shows what's running, loaded, and on disk
.venv/bin/tutor --help     # chat, course, ingest, prompts, models, languages, bench, …
```

The web app is served on `http://127.0.0.1:8765`. The backend only runs when
you start it, stops itself after 30 idle minutes (an open, visible tab counts
as use), and installs no launch agents, login items or cron jobs.

## Your setup

| What | Where |
|---|---|
| About you (read before every conversation) | `~/.config/magnus-tutor/profile.yaml`, or Settings → About you |
| Resume (optional) | put `resume.pdf` in `~/.config/magnus-tutor/`, then Settings → *Read my resume* |
| Courses (plain YAML, one folder each) | `~/.config/magnus-tutor/courses/<slug>/course.yaml` |
| Prompts (plain Markdown, edit any time) | `~/.config/magnus-tutor/prompts/`, per-course overrides in `courses/<slug>/prompts/` |
| Settings | `~/.config/magnus-tutor/settings.yaml` (only your overrides), or the Settings page |
| Generated data (SQLite, page cache) | `~/.local/share/magnus-tutor/` |

**Adding courses:** *New course* (Home or ⌘K) reads a syllabus and proposes
the course (title, code, topics, languages, notation conventions); you can
also use `tutor course add`. At the end of a term, the same page archives
last term's courses in one click. Archived courses stay searchable ("all
courses, incl. archived" in the Library).

**Materials folders** (read in place, never copied; the backend watches them):

```
~/Documents/Magnus/notes/<course>/       ← handwritten notes exported as PDF
~/Documents/Magnus/textbooks/<course>/   ← textbook PDFs
~/Documents/Magnus/other/<course>/       ← syllabus, slides, problem sets
```

Notes apps like GoodNotes can export straight there (*Share → Export → PDF →
Save to Files*). With *Desktop & Documents* in iCloud, exports from an iPad
land there too.

## How it works

### Office hours: gates in code, not just in the prompt

Local models follow instructions less reliably, so the engine enforces the
rules and tells the model what this turn may reveal (each toggle in Settings):

- **Attempt gate:** the first reply to a new problem asks for your attempt.
- **Hint ladder:** 0 nothing · 1 which concept · 2 which method · 3 the next
  step · 4 full solution. At most one level per "I'm stuck".
- **Full-solution gate:** only on explicit request ("show me the solution" or
  the button), after asking "now, or after one more attempt?".
- **Output check:** replies stream sentence by sentence; a sentence that would
  reveal the final answer (as text, a number, or an equivalent expression), a
  later step, or most of the reference code is withheld and the reply is
  rewritten more strictly.
- Answers are checked with SymPy/numeric comparison against the reference.
  A different valid method is fine: the result is what gets checked.
- Code: real run results (tests pass/fail) are the ground truth.

Every attempt is logged (`attempts`: concepts, hint level reached, solved,
used solution) for mastery tracking now and knowledge tracing later.

### The hidden solver

When you paste a problem, the solver model works it privately (thinking on,
capped budget), returns JSON (answer, SymPy form, units, steps, concepts,
common mistakes, checks), and the answer is verified:

- **math:** the solver's declarative checks evaluated with SymPy (no code is
  executed): numeric recomputation, derivatives, integrals, solve, limits;
- **physics:** dimensional analysis with `pint` from a symbol→unit map, plus sanity checks;
- **code:** the reference program runs in the sandbox against its tests
  (only if you turn that on in Settings).

Confidence is **verified** (a check reproduced the final answer and nothing
failed), **agreed** (independent runs agree), or **uncertain** (the tutor says
so and checks steps with you). An unverified first run gets one extra run.
Tutor turns always win: the solver is cancelled the moment you send a
message and resumes afterwards, so it never delays a reply.

### Notes and textbooks

Text comes from three tiers, cheapest first:

1. the PDF's text layer (`pymupdf4llm` Markdown, ~0.06 s/page);
2. **macOS Vision OCR** (Neural Engine, ~0.13 s/page) for scanned pages and for
   textbook pages whose equations dropped out of the text layer. Many textbooks
   draw math as glyphs with no text, so extraction reads "a surface of area
   that is perpendicular"; OCR recovers inline symbols and values;
3. the **vision model** for handwritten notes (asks first when a notebook has
   more than 10 handwritten pages, with a time estimate; ~15 s/page on an M5)
   and for *equation repair* of textbook pages you actually cite (queued, only
   when idle and plugged in). Transcriptions are cached by page-image hash and
   never redone.

Textbooks are chunked by section using the PDF bookmarks, with the heading
path and the printed page number (inferred from page headers when the PDF has
no page labels), so citations match the physical book: *University Physics
Volume 2 §6.3, p. 245*. End-of-chapter exercises are indexed: "chapter 6,
problem 12" pulls the exact problem (reading that page properly if symbols are
missing). You can fix any transcription in the Library against the page image.

*Why not Marker?* It needs PyTorch and several GB of models and is slow
without a big GPU; on a laptop the OCR tier recovers most of what plain
extraction drops at a tiny fraction of the cost, and the vision model handles
the rest only where it matters.

Retrieval is hybrid: SQLite FTS5 keywords + `sqlite-vec` vectors
(`qwen3-embedding:0.6b`), merged by reciprocal rank fusion, scoped to the
course by default.

### Code sandbox

Runs in a temp folder under a macOS `sandbox-exec` profile (no network, no
reading your home folder or /Volumes, writes only inside the temp folder, no
forking unless a language sets `allow_fork: true`) with CPU, file size and
open-file limits, and a watchdog that kills the run on timeout or memory over
512 MB. Python runs in the sandbox's own venv, never the tutor's. Code the
tutor suggests opens in the editor (*Open in editor*) and runs only when you
press Run.

Languages live in `~/.config/magnus-tutor/languages.yaml`. To add one:
install the toolchain, copy an entry (extensions, `run` or `check` command,
editor highlight, hello program), add it to the course's `languages:`, and
run `tutor languages check`. A command can use a full path, so a toolchain
doesn't have to be on your `PATH`: the default Ruby entry uses Homebrew's
unlinked Ruby (`/opt/homebrew/opt/ruby/bin/ruby`), leaving your shell's `ruby` unchanged.

## Models and resources

`tutor init` (or `tutor hardware`) checks your chip and RAM and recommends a
preset:

- **Light:** `qwen3.5:4b` everywhere, solver only on demand.
- **Standard** (default):
  - **`qwen3.5:9b`** for tutor, solver and vision (thinking off for tutor
    turns, on with a ~2,500-token budget for the hidden solver);
  - **`qwen2.5-coder:7b`** for code turns, loaded only when needed (the 9B
    model is unloaded first, so they're never in memory together);
  - **`qwen3-embedding:0.6b`** for retrieval.
- **Full:** separate models per role and 3 solver runs; needs a lot more RAM.

Any role can be pointed at another Ollama model in Settings. Models are only
ever pulled after showing their size. Settings shows what's loaded, RAM, and
background jobs, with *Unload models now* and *Pause all background work*;
heavy jobs run only when plugged in (default on).

### Measured on an Apple M5 with 24 GB (other apps using ~15 GB)

| Model | Resident | Speed | Answers right, no thinking (math/physics + code) | Code problems |
|---|---|---|---|---|
| qwen3.5:9b | 5.9 GB | 21 tok/s | 17/31 | 6/11 |
| qwen2.5-coder:7b | 4.6 GB | 26 tok/s | – | **11/12** |
| qwen3.5:4b | 3.1 GB | 37 tok/s | 4/14 (partial run) | – |
| qwen3:14b / deepseek-r1:14b | 9.3–9.4 GB | 13 tok/s | not run: too slow, pushed swap to 3.7 GB | – |
| gemma4:12b | 8 GB | 22 tok/s | not run: over the memory budget | – |

A tutor reply while the hidden solver is mid-pass: **65 s** to the first word
if both go straight to Ollama, **0.16 s** with the solver yielding (the default
policy), the same as with no solver running. Full numbers:
[`bench/results/REPORT.md`](bench/results/REPORT.md).

| | Measured |
|---|---|
| Idle, tutor stopped | nothing running, no model memory (`tutor doctor`) |
| Backend running, no model loaded | ~70–90 MB |
| During a session (model loaded) | tutor + Ollama ~6.7 GB; CPU peaks ~80% of one core while generating |
| After the keep-alive (3 min) | model unloaded automatically, memory back to baseline |
| After `tutor stop` | no tutor or Ollama processes, no login items/launch agents/cron |
| Retrieval | ~25 ms median |
| Warm reply | ~21 tokens/s; first token ~1–3 s without sources, ~3–7 s with retrieved passages |
| Textbook ingestion | ~2 min for 156 pages (one-time, background priority) |

**Benchmarks:** `tutor bench run|latency|concurrency|retrieval|report`. The
problem set (`bench/problems.yaml`, 32 E&M, quantum, SML and cloud problems
with answers verified in SymPy) and the retrieval set (`bench/retrieval.yaml`,
built on [OpenStax *University Physics Vol. 2*](https://openstax.org/details/books/university-physics-volume-2))
are starting points; replace or extend them with problems from your own
courses to pick models for your machine.

## Optional: Magnus integration

Magnus Tutor was built alongside Magnus, a personal terminal companion app.
Everything above works without it; the header timer just shows
*timer offline*.

With Magnus installed, Magnus owns the one focus timer, stored in
`~/.config/magnus/timer.json` (contract: [docs/timer-contract.md](docs/timer-contract.md)).
The tutor shows it in the header pill and tab title and controls it through
`magnus timer …` (set `magnus.command` in `settings.yaml` if it isn't on your
`PATH`). It never writes the timer file itself, so focus minutes are logged
once, by Magnus. Phase-end alerts: Settings → Focus timer. The timer contract
is small and documented, so another timer app could implement it too.

## Optional cloud model

Off by default. Save an Anthropic API key (Settings; stored in the macOS
Keychain), enable it, and an *escalate* link appears on replies. Only the
replies you escalate are sent to Anthropic (`claude-opus-5-5`), along with the
context that reply was built from: the system prompt (including your *About
you* profile and the course details), recent conversation history, any images
you attached, the retrieved passages from your materials, and in office
hours the problem and the solver's reference solution. Escalated replies go through the same hint
gates and leak check, are marked with a cloud badge, and the header shows
*cloud on* while it's enabled.

## Security model

Single user, localhost only. The backend accepts requests only with a
`127.0.0.1`/`localhost` Host header (blocks DNS rebinding), refuses any request a
browser marks as cross-site (`Sec-Fetch-Site`), refuses state-changing requests
from other web origins or with non-JSON bodies, and sends a strict CSP. Settings
that name programs, hosts or folders (`magnus.command`, `ollama.host`, course
folders) can only be changed by editing the YAML files. Math from students and
models is parsed against an allowlist and never `eval`ed. Code runs in the
sandbox described above (also denied LaunchServices, the clipboard, AppleEvents,
`open`, `osascript`, `launchctl`, `security`, and forking, so nothing it starts
can outlive the run). The hidden solver only runs its own reference code when you
turn that on in Settings. Every PDF (and uploaded HEIC/WebP image, syllabus,
resume) is parsed in a separate sandboxed worker process with no network, no
access to your files beyond that one, and time limits. Dependencies are pinned
in `uv.lock`. Model replies can't load outside images. The optional API
key lives in the Keychain and is passed to `security` on stdin.

## Development

```sh
uv sync --extra dev
.venv/bin/pytest -q                        # backend tests (fake model; no GPU)
MAGNUS_TIMER_BIN=…/magnus/bin/magnus.js .venv/bin/pytest tests/test_timer_bridge.py   # against a real Magnus timer
cd web && npm run dev                      # UI with hot reload (proxy to :8765); start the backend with
                                           #   MAGNUS_TUTOR_DEV_ORIGIN=1 so it accepts the dev server's origin
MAGNUS_TUTOR_HOME=/tmp/x .venv/bin/tutor … # use a throwaway config/data dir
MAGNUS_TUTOR_NO_MANAGE=1                   # backend doesn't start/stop Ollama (dev)
```

The color themes in `web/src/styles/themes.css` are generated from Magnus's
terminal themes by `scripts/build-themes.mjs` and committed, so building the
app doesn't need Magnus.

Layout: `src/magnus_tutor/` (engine/, ingest/, llm/, server/, tools/, prompts/defaults/),
`web/` (React + Vite + TypeScript, KaTeX, CodeMirror), `bench/`, `docs/`, `tests/`.
