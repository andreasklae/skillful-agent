---
name: greeter
description: Generate personalised greeting messages for a given name and style.
---

# Greeter Skill

Use this skill to generate friendly, personalised greetings.

## Usage

Run `greet.py` to produce a greeting. It accepts a `--name` (the person to greet)
and an optional `--style` (one of `formal`, `casual`, `enthusiastic`; default `casual`).

```
scripts/greet.py --name Alice --style formal
```

The script prints a plain-text greeting to stdout.
