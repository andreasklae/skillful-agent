# Skill-Writing Conventions — Quick Reference

Distilled from the skill-creator skill. Consult the full skill-creator SKILL.md for details.

---

## Frontmatter (Required)

Every SKILL.md starts with YAML frontmatter:

```yaml
---
name: my-skill-name
description: >
  What this skill does and when to trigger it. Be specific and slightly "pushy"
  — include trigger phrases, contexts, and edge cases so the agent doesn't
  under-trigger. All triggering logic lives here, not in the body.
---
```

- `name` and `description` are required.
- `compatibility` is optional (list required tools or dependencies).
- The description is the **primary triggering mechanism**. The agent sees name + description in its skill list and decides whether to invoke. Make it count.

## Description Tips

- Include both what the skill does AND when to use it.
- List specific trigger phrases and contexts.
- Err on the side of "pushy" — under-triggering is worse than over-triggering.
- Cover edge cases and near-miss scenarios.
- Example: Instead of "Helps with APIs", write "Use when the user asks to integrate, connect to, call, or wrap any REST or GraphQL API — including authentication, pagination, error handling, and SDK generation."

---

## Directory Structure

```
skill-name/
├── SKILL.md                # Required. Core instructions.
├── client_functions.json   # Optional. Client-side functions the agent can request.
├── permissions.yaml        # Optional. Default permission rules for this skill's operations.
├── scripts/                # Executable code for deterministic/repetitive tasks
├── references/             # Docs loaded into context as needed
├── assets/                 # Files used in output (templates, icons, fonts)
├── docs/                   # Supplementary documentation (knowledge bases)
└── tests/                  # Test cases and results
```

Only `SKILL.md` is required. Add other directories and files as needed.

---

## Client-Side Functions (`client_functions.json`)

Skills that expose operations which should be gated by the client (e.g. write operations
on external APIs, destructive actions, operations needing user confirmation) should declare
a `client_functions.json` file.

```json
[
  {
    "name": "request_permission",
    "description": "Request user permission to execute a write operation.",
    "awaits_user": true,
    "parameters": [
      { "name": "operation", "type": "string", "description": "The operation name.",  "required": true },
      { "name": "domain",    "type": "string", "description": "The domain/category.", "required": true },
      { "name": "action",    "type": "string", "description": "The action type (insert, update, delete, etc.).", "required": true }
    ]
  }
]
```

The SKILL.md body should instruct the agent to call `call_client_function` before any
write operation, passing the exact operation name, domain, and action type. After calling
it with `awaits_user: true`, the agent must stop and wait for the user's next message.

---

## Permission Manifest (`permissions.yaml`)

For skills that expose APIs or services with write capabilities, include a `permissions.yaml`
that declares the default allow/deny rules for the skill's operations. This is loaded by
the client application and controls which operations require user approval.

```yaml
default_allow: false

rules:
  # Read operations are pre-approved — no user prompt needed
  - domains: ["*"]
    actions: ["query", "read", "search", "count"]
    allow: true
  # Write operations require user approval (default_allow: false handles this)
```

**Important:** `permissions.yaml` is client-controlled configuration. The agent may
*create* it (when it doesn't exist yet during scaffolding), but can never *overwrite*
an existing one. Users must edit it directly. This is enforced by the SDK's
`write_skill_file` tool and the `write_skill_content.py` script.

---

## Progressive Disclosure

Three loading levels — use them to manage context window size:

1. **Metadata** (name + description) — Always in context (~100 words). This is what triggers the skill.
2. **SKILL.md body** — Loaded when the skill triggers. Keep under 500 lines ideally.
3. **Bundled resources** (scripts/, references/, docs/) — Loaded on demand. Unlimited size. Scripts can execute without being loaded into context.

When a skill supports multiple domains, organize by variant in `references/` and load only the relevant file.

---

## SKILL.md Body — Writing Guidelines

### Structure
- Lead with the most important information.
- Use clear headers for phases, sections, or operations.
- Include examples where they clarify behavior.
- For large reference needs, point to files in `references/` or `docs/` with guidance on when to read them.

### Style
- **Imperative form**: "Read the config file" not "You should read the config file".
- **Explain why**: Theory of mind beats rigid MUSTs. Tell the model *why* something matters so it can handle edge cases intelligently.
- **Examples over abstractions**: Show concrete input/output pairs.
- **Use headers for navigation**: The model skims — make sections findable.

### Length
- Under 500 lines is ideal for SKILL.md body.
- If approaching the limit, add hierarchy: move detailed content to `references/` files and add clear pointers.
- For reference files over 300 lines, include a table of contents.

---

## Scripts

- Place in `scripts/` directory.
- Make them executable and self-contained where possible.
- Include argument parsing (argparse or similar).
- Add a brief docstring or `--help` output.
- Scripts can be run without loading their source into the context window.

---

## Output Format

If the skill produces structured output, define the template explicitly:

```markdown
## Report Structure
ALWAYS use this exact template:
# [Title]
## Summary
## Findings
## Recommendations
```

---

## The Principle of Least Surprise

A skill's contents should not surprise the user in their intent if described. The skill should do what it says, nothing more, nothing less. No hidden behaviors, no malware, no exploits.

---

## Anti-patterns to Avoid

- **Heavy-handed MUSTs**: Explain reasoning instead. The model is smart enough to follow intent.
- **Overly narrow examples**: Make skills general. Don't overfit to specific test cases.
- **Keyword-only descriptions**: Write descriptions that capture *intent*, not just keywords.
- **Monolithic SKILL.md**: Break large skills into SKILL.md + reference files.
- **Missing triggering context**: If the description doesn't say when to trigger, the skill won't trigger.
