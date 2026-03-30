---
name: learner
description: >
  Meta-skill for acquiring persistent, reusable knowledge and packaging it as a
  new skill. Use when the user explicitly asks the agent to LEARN something and
  retain it — meaning acquire a new capability or body of knowledge and produce
  a functional skill with documentation, scripts, and a navigable knowledge base.

  Trigger phrases: "learn how to...", "figure out how to...", "teach yourself...",
  "become capable of...", "I want you to know how to...", "can you learn about...",
  "build a skill for...", "create a skill that knows...", "add the ability to...".

  Covers all types of learning: knowledge acquisition (history, science, business
  domains), API integrations (REST, GraphQL), MCP server connections, CLI tools,
  local system commands, workflows, multi-step processes, libraries, and frameworks.

  DO NOT trigger on general knowledge questions or research requests like "tell me
  about X", "do research on Y", "explain how Z works", "what is X?" — those are
  normal conversational requests that don't need a skill. The distinction is that
  "learn" implies the user wants a persistent, reusable capability packaged as a
  skill, not just an answer to a question.
---

# Learner

A structured process for researching a topic, acquiring knowledge, and packaging
what you learned as a fully functional skill. The output is a new skill directory
with documentation, scripts, and a navigable knowledge base.

Before doing anything else: **read the skill-creator skill** (invoke it via the
Skill tool). It defines the conventions for writing skills — frontmatter format,
directory structure, description writing, progressive disclosure. You need to
understand those conventions before you can produce good output. Also read
`references/skill-conventions.md` in this skill's directory for a compact summary.

---

## Phase 0 — Intake & Scoping

Do all of this before any research. Getting the scope right saves enormous
amounts of tokens.

### 0.1 Confirm Intent

Even when triggered, always confirm with the user first:

> "Do you want me to actually learn this and create a skill for it, or would you
> just like me to answer your question / do some quick research?"

The full learning workflow is token-intensive and produces a persistent skill.
The user might just want a quick answer. Only proceed if they confirm they want
a skill.

### 0.2 Clarify Scope

Ask the user what exactly they want to learn. If the request is vague or
ambiguous, ask for clarification BEFORE starting any research. Think about the
XY problem — spar with the user to understand what they actually want to achieve.

Good clarifying questions:
- "Do you want the full API or just authentication?"
- "Should I focus on the Python SDK or the raw REST API?"
- "How deep — overview or comprehensive reference?"
- "Are there specific use cases you have in mind?"
- "Do you need scripts that automate this, or just documented knowledge?"

### 0.3 Check for Existing Skills

Look at currently available skills. If a skill for this topic already exists,
don't make a duplicate. Instead, report:

> "I already have a skill for X. Would you like me to review and update it, or
> expand it with additional knowledge?"

If the user says yes, use the skill-creator's review/update workflow — don't
reinvent the wheel.

### 0.4 Assess Feasibility

Be honest about what you can and can't do. If you can't fully operationalize
something, say so clearly:

> "I can learn the concepts of real-time 3D rendering and write helper scripts,
> but I can't actually run a GPU-accelerated renderer in my environment."

Don't just "try and make the most of it" unless the user explicitly says to.

### 0.5 Estimate Complexity

Tell the user roughly how involved this will be:
- **Straightforward**: "I just need to read the API docs and write a wrapper."
- **Moderate**: "I'll need to research several sources and build scripts."
- **Broad**: "This is a wide topic that will require significant research across
  multiple sources. Expect this to be token-intensive."

### 0.6 Set Up Tracking

Add structured checkpoints to the todo list. Example items:

- "Clarify scope with user"
- "Survey available skills for research tools"
- "Scaffold learned skill directory"
- "Identify and research 3+ sources on [topic]"
- "Save relevant docs to docs/ folder"
- "Write SKILL.md for learned skill"
- "Write scripts (if applicable)"
- "Test scripts and validate"
- "Report results to user"

Update the todo list as you progress through phases.

---

## Phase 1 — Inventory & Planning

### 1.1 Survey Available Skills

Check what other skills are currently available. Any skill that could help with
research or building should be used — not just web search. Examples:
- Skills for reading files, querying databases, interacting with GitHub
- Skills for specific platforms, APIs, or data sources
- MCP servers with relevant capabilities (context7 for library docs, etc.)

Use the full toolkit, not just the learner's own resources.

### 1.2 Identify Dependencies

If learning X requires a capability you don't have, recognize it and propose
creating a sub-skill first:

> "To properly research this topic, I'd need to be able to query the PubMed
> database, but I don't have that capability yet. Should I create a skill for
> that first?"

ALWAYS get user confirmation before branching into sub-skill creation. If
approved, run the full learner workflow for the sub-skill before continuing
with the original task.

### 1.3 Plan Research Strategy

Decide what sources to look for and in what order. Distinguish between:
- **Load into context**: Core docs needed to write scripts or understand the
  topic deeply. Load these when you're actively working with them.
- **Save to disk**: Supplementary material for future reference. Save these to
  `docs/` without loading them fully.

---

## Phase 2 — Research & Knowledge Acquisition

### 2.1 Choose Placement

Before creating anything, explore the existing skill tree to decide where the
new skill should go. The **user skill directories** are listed in the system
prompt — pass them to `list_skill_tree.py`:

```
run_script(skill_name="learner", filename="list_skill_tree.py",
           args='{"roots": ["/path/to/Research", "/path/to/Learning"]}')
```

Use the paths from the "User skill directories" section of the system prompt.

This shows all existing categories and skills with their paths. Use it to
reason about placement:

- **Does an existing category fit?** A new API research skill might belong in
  `Research/` alongside wikipedia and snl. A workflow tool might belong in
  `Learning/` or a new subcategory.
- **Subcategories are fine.** Categories are just directories without a
  SKILL.md — the recursive scanner finds skills inside them at any depth.
  You can nest freely within existing top-level directories.
- **New top-level categories** require a config change to be discovered.
  Prefer placing skills inside existing top-level directories.
- **Default fallback**: If unsure, use a `Learned-skills/` subdirectory
  inside one of the user skill directories.

### 2.2 Scaffold the Skill Directory

Use the `scaffold_skill` tool — it creates the directory skeleton AND
registers the skill in one call. No separate `register_skill` needed.

```
scaffold_skill(skill_name="my-skill", base_dir="/path/to/category")
```

- `skill_name` — kebab-case name (required)
- `base_dir` — parent directory where the skill folder will be created.
  Pick a path from the "User skill directories" section of the system
  prompt, or append a subdirectory (e.g. `.../Research` or `.../Learned-skills`).
  If omitted, defaults to the first user skill directory.

The tool returns the absolute path to the new skill. Use that path in all
subsequent `write_skill_file` and `run_script` calls.

Only use the manual `register_skill` tool if you created directories
yourself via `write_skill_file` instead of `scaffold_skill`.

### 2.3 Saving Research to Docs

Use `save_doc.py` to fetch content from a source and write it directly to
the skill's `docs/` folder. The content never enters the context window —
this is the primary mechanism for "save, don't load":

```
run_script(skill_name="learner", filename="save_doc.py",
           args='{"skill_path": "/path/to/skill", "source": "wikipedia",
                  "query": "Oseberg ship", "language": "en"}')
```

Supported sources:
- `wikipedia` — Fetch a full article by title. Args: `query` (title),
  `language` (default "en")
- `url` — Fetch raw content from a URL. Args: `query` (the URL)
- `text` — Write arbitrary text. Args: `query` (the content),
  `filename` (required)

After saving, update `docs/index.md` with `write_skill_file`.

### 2.4 Writing Files

Use the `write_skill_file` tool to write or update any file in the new
skill — SKILL.md bodies, scripts, docs/index.md entries, anything:

```
write_skill_file(file_path="/abs/path/to/file.md", content="file content")
```

Set `append=True` to append instead of overwrite. This tool creates parent
directories automatically.

**Always use `write_skill_file` for writing files**, not `run_script` with
`write_skill_content.py`. The `write_skill_file` tool handles large content
and special characters reliably because it avoids double-encoding issues.

### 2.5 Execute Research

Use all available tools and skills identified in Phase 1. This is the phase
where you actually gather knowledge. Use `save_doc.py` to save sources
directly to disk without loading them into context.

### 2.6 Context Window Management

This is critical. The default behavior is **save, don't load**:

- When finding a potentially useful source, use `save_doc.py` to fetch and
  save it directly — the content never enters the context window.
- Only load content (via `read_reference` or reading files) when it is
  actively needed for the current step. For example: load API documentation
  when you're about to write a script that calls that API, not during the
  initial survey.
- Think: "This might be useful later, so I'll save it, but I won't read it
  now — I'll look it up when it's relevant."

The `docs/index.md` file is your table of contents. It describes what each
saved document contains, so you (or a future session) can find and load the
right file on demand without reading everything.

### 2.7 Source Critique

For every source, assess and record its reliability:

- **Official documentation**: Highly reliable, authoritative.
- **Peer-reviewed research**: Reliable, but may be domain-specific.
- **Reputable publications/tutorials**: Generally reliable, check date.
- **Blog posts / Stack Overflow**: May be outdated or opinionated.
  Cross-reference with official docs.
- **Wikipedia on contested topics**: Multiple perspectives may exist.
  Note this explicitly.

Record the assessment in `docs/index.md` next to each entry.

### 2.8 Confidence Tagging

Tag each piece of gathered information with a confidence level and brief
reasoning:

- **High**: From official docs, verified by testing, or from multiple
  agreeing authoritative sources.
- **Medium**: From reputable but potentially outdated sources, or not yet
  verified by testing.
- **Low**: From a single unofficial source, conflicting information exists,
  or the topic is contested.

This goes into the docs and ultimately into the learned skill's SKILL.md
frontmatter.

---

## Phase 3 — Skill Synthesis

### 3.1 Use the Skill-Creator

Invoke the skill-creator skill for formatting and structural guidance. It
defines the conventions you must follow:

- Frontmatter format (name, description required; plus the learner-specific
  fields: sources, status, learned_date, confidence, source_reliability)
- Directory structure
- Progressive disclosure
- Description writing (be "pushy" for triggering)
- Writing style (imperative form, explain *why*)

See `references/skill-conventions.md` in this skill's directory for a compact
summary of these conventions.

### 3.2 Write the SKILL.md

Fill in the scaffolded SKILL.md with:

1. **Frontmatter**: Update all fields — name, description, sources, status
   (leave as "draft"), learned_date, confidence, source_reliability.
2. **Body**: Write the skill instructions following conventions:
   - Imperative form ("Run this command" not "You should run")
   - Explain *why* things matter — the model is smart
   - Include examples where they clarify behavior
   - Keep under 500 lines; use `references/` for overflow
3. **Scripts**: Write any executable scripts to `scripts/`. These automate
   deterministic tasks (API calls, file transformations, data lookups).
4. **References**: Move large reference material to `docs/` or `references/`
   with clear pointers from SKILL.md about when to read each file.

### 3.3 Update docs/index.md

Make sure the document index is complete. Every file in `docs/` should have
an entry with: file name, description, source, reliability, confidence.

---

## Phase 4 — Validation & Testing

### 4.1 Test Everything Testable

Run scripts, verify API calls return expected responses, check that CLI
commands work, validate outputs against expectations.

### 4.2 Iterate on Failures

If a test fails, don't just report it — try to debug and fix it:
- Read error messages carefully
- Check the docs again
- Rewrite the script or try a different approach

**But set a limit**: after 2–3 meaningful attempts at fixing the same issue,
stop and report to the user. Burning tokens in an endless debug loop helps
no one. Make the report informative — include error messages, what you tried,
and what you think the problem is — so the user can guide you.

### 4.3 Communicate Blockers

If testing is blocked by something you can't resolve, tell the user
immediately and specifically:

- **Missing API key**: "Add `OPENAI_API_KEY` to your .env file"
- **Missing credentials**: Explain exactly what's needed and where
- **Missing environment capabilities**: Explain the limitation
- **Missing dependencies**: List what needs to be installed

Never silently skip blocked tests.

### 4.4 Record Results

Write results to `tests/test_results.md`:

- What was tested
- What passed (with evidence — command output, response snippets)
- What failed and what was tried to fix it
- What couldn't be tested and why
- What the user needs to do to unblock remaining tests

### 4.5 Update Status

Update the `status` field in the learned skill's SKILL.md frontmatter:

- **draft** — Created but not tested, or tests failed
- **tested** — Phase 4 passed, scripts work, knowledge verified
- **verified** — Reserved for when the user confirms the skill works in
  practice

### 4.6 Final Report

Summarize for the user:

1. What was learned
2. Confidence levels and source reliability
3. Test results (what works, what doesn't, what needs user action)
4. Any follow-up actions needed
5. Where the new skill lives and how to use it

---

## Cross-Cutting Principles

Follow these throughout all phases.

### Token Frugality

This skill can be expensive. Be conscious of token usage:
- Ask before doing expensive operations ("This will require reading several
  long documents — should I proceed?")
- Clarify scope BEFORE diving into research
- Save to disk by default, load into context only when needed
- Don't research broadly when the user asked for something narrow
- When even slightly unsure, ask — wasting tokens on the wrong thing is
  worse than asking a question

### User Communication

- Ask for clarification when unsure about scope, depth, or direction
- Report blockers immediately — don't work around missing credentials silently
- Request API keys, credentials, or permissions explicitly
- Confirm with the user before creating sub-skills or doing expensive research
- Communicate complexity estimates early
- Never silently skip something — if it can't be done, say why

### Todo List Usage

Use the built-in todo list throughout. Add structured checkpoints with clear
deliverables at each phase, not vague items. Update as you progress.

### Cross-Skill Leverage

Always check what other skills are available and use them when they'd help.
The research phase especially benefits from any available skill — not just web
search. MCP servers, database tools, GitHub integrations, platform-specific
skills — all fair game.

### Honesty and Feasibility

Be upfront about what you can and can't do. Don't hallucinate capabilities.
Confidence and reliability tagging should be present throughout — in the
research, in the skill output, and in the final report.

### Dependency Chaining

If a prerequisite skill is needed, propose it, get user confirmation, create
it first using this same learner workflow, then continue with the original task.

### Incremental Learning

You can extend a previously learned skill rather than replacing it:

> "You already learned the basics of FFmpeg — now also learn about
> hardware-accelerated encoding."

This is additive — build on the existing skill's docs, scripts, and SKILL.md
rather than starting over.
