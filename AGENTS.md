# AI Project Instructions

## Project Overview

This repository contains a Godot project and a persistent project knowledge base.

The repository structure is:

* `(Game Name)/` — the Godot project
* `Knowledge/` — the project's Obsidian knowledge vault

The `Knowledge/` directory is part of this Git repository and is intended to preserve the project's architecture, design decisions, system relationships, implementation details, and other context useful to future AI coding agents.

---

# Core Rules

## 1. Inspect Before Modifying

Before making a significant change:

1. Inspect the relevant source code.
2. Inspect the relevant Godot scenes and resources.
3. Search the `Knowledge/` directory for relevant project knowledge.
4. Use the existing architecture and documented decisions when planning the implementation.

Do not assume that a system works a particular way without checking the project.

---

## 2. Code Is the Source of Truth

The actual Godot project is authoritative.

If the Knowledge vault contradicts the current code or scene files:

* Treat the actual project files as correct.
* Identify the documentation discrepancy.
* Update the relevant Knowledge documentation when appropriate.

Never modify working code simply to make it match outdated documentation.

---

## 3. Do Not Invent Project Information

Never create documentation describing systems, relationships, architecture, design decisions, or behaviour that does not actually exist.

If something cannot be determined from the project:

* Say that it is unknown.
* Do not guess.
* Do not create fictional architecture.

This is especially important when the project is still being established.

---

# Knowledge Base

The Knowledge vault is located at:

`Knowledge/`

Its current structure is:

```text
Knowledge/
├── 00 Project/
├── Architecture/
├── Systems/
├── Scenes/
├── Code/
├── Data/
├── Decisions/
└── Changes/
```

Use these directories according to their purpose.

### 00 Project

High-level information about the game and project.

### Architecture

How major parts of the project are structured and how they interact.

### Systems

Descriptions of gameplay and technical systems.

### Scenes

Godot scene structures, important nodes, scene relationships, and scene-specific behaviour.

### Code

Important scripts, their responsibilities, dependencies, and relationships with other code.

### Data

Resources, data structures, configuration, and important persistent state.

### Decisions

Architectural and design decisions, including why a particular approach was chosen.

### Changes

Records of significant changes and their effects on the project.

---

# Understanding Architecture

When implementing a feature, consider more than the file being edited.

Determine whether the change affects:

* Other scripts
* Scenes
* Nodes
* Signals
* Resources
* Autoloads
* Global state
* Data flow
* Dependencies
* Existing systems
* Architecture decisions
* Existing documented assumptions

A seemingly small code change may affect multiple parts of the project.

---

# Scene Awareness

Godot scene structure is part of the project's architecture.

When modifying a scene:

* Inspect its existing node hierarchy.
* Understand which scripts are attached to relevant nodes.
* Check important node paths.
* Check signals and connections.
* Check references between scenes and resources.
* Preserve existing relationships unless the task requires changing them.

Do not restructure a scene simply for convenience without considering its documented architecture.

---

# Documentation

Documentation should explain both:

### What

What the code, scene, or system does.

### Why

Why it is structured that way and what other parts of the project depend on it.

The "why" is particularly important because future coding agents need to understand the reasoning behind architectural decisions.

Do not document every trivial implementation detail. Focus on information that helps an agent safely understand and modify the project.

---

# Changes

For significant changes, identify:

* What changed
* Why it changed
* Files affected
* Scenes affected
* Systems affected
* Dependencies affected
* Architectural consequences
* Important new behaviour

The automated change-analysis system that will eventually be added to this repository will use this information to keep the Knowledge vault synchronized with the project.

---

# Validation

After making changes:

1. Check for syntax or parse errors.
2. Run appropriate tests or validation available for the project.
3. Check that modified scenes and resources remain valid.
4. Consider whether related systems are affected.
5. Do not claim that something was tested if it was not actually tested.

---

# Codex Post-Change Workflow

For a completed, meaningful feature or fix, run the repository's automation after the implementation has been validated and committed. Do not run it on half-finished work.

1. **Inspect and implement:** follow the rules above; inspect relevant code, scenes, and Knowledge before editing.
2. **Validate:** run the available project checks and tests. Clearly report anything that could not be tested, including Godot validation if Godot is unavailable.
3. **Commit boundary:** the analyzer reads Git commits, not uncommitted diffs. If the feature is not committed, do not pretend the pipeline has analyzed it. Ask before creating a commit if the user has not authorized committing; never push without explicit permission.
4. **Analyze and inspect:** from the repository root, run:
   `python .ai/change_analyzer.py`
   `python .ai/change_inspector.py`
   Check their output and confirm the expected commit has a change record and a current, Inspector-fingerprinted report.
5. **NotebookLM review:** run `python .ai/notebooklm_integration.py`. This requires the user's locally installed `notebooklm-py`, authentication, and an explicitly configured project notebook. If unavailable or authentication fails, stop this stage, report the exact blocker, and continue only with stages that do not assume a NotebookLM review exists.
6. **Build proposal:** run `python .ai/knowledge_updater.py`. Inspect the generated proposal and its evidence. NotebookLM text is a recommendation, not authority; code, scenes, project settings, and Git evidence are authoritative.
7. **Safely apply:** only for a proposal with status `UPDATE_PROPOSED` and a valid `## Application Plan`, first run:
   `python .ai/knowledge_applier.py .ai/proposals/<proposal-file>.md --dry-run`
   Review the result. If the dry run succeeds and each proposed fact is appropriate, run the same command without `--dry-run`. Do not apply `REQUIRES_REVIEW`, malformed, stale, or unsupported proposals. `NO_UPDATE_REQUIRED` does not need applying.
8. **Sync updated Knowledge:** after any successful Knowledge application, run `python .ai/notebooklm_integration.py sync` so NotebookLM receives the updated Markdown. If NotebookLM is unavailable, leave the local Knowledge update intact and report that sync remains pending.
9. **Review the final diff:** check `git diff --check`, inspect all modified files, and run relevant tests. Keep generated reports, proposals, review records, application records, and state files if they are part of the intended automation workflow. Never discard user changes.
10. **Report honestly:** summarize the feature, validation, automation stages completed or blocked, Knowledge changes applied, and any remaining manual step. Do not claim a review, application, or sync succeeded without successful command output.

Do not push changes unless the user explicitly asks. Do not apply Knowledge recommendations merely because NotebookLM suggested them. The Applier's checks are required safeguards, not a substitute for reviewing the proposal.

# Knowledge Automation Overview

The intended workflow is:

```text
User request
    ↓
Codex reads project + Knowledge
    ↓
Implement feature and validate
    ↓
Commit feature (only when authorized)
    ↓
Change Analyzer → Change Inspector
    ↓
NotebookLM reviews the inspection + Knowledge
    ↓
Knowledge Updater creates a verified proposal
    ↓
Codex reviews proposal → Applier dry-run → apply if safe
    ↓
Sync updated Knowledge to NotebookLM
    ↓
Review final diff and report results
```

The Python scripts are local repository tools. NotebookLM stages depend on the user's local CLI installation, authentication, network access, and configured notebook. If any stage is blocked, report it and do not fabricate its output or skip a required safety check.

# General Principle

The goal is not to produce the smallest possible code change.

The goal is to make changes that are consistent with the project's existing architecture and remain understandable to future developers and AI agents.

When uncertain:

1. Inspect the project.
2. Search the Knowledge vault.
3. Follow documented architectural decisions.
4. Prefer existing project patterns.
5. State uncertainty rather than inventing information.
