# AI Maintenance Workspace

This directory contains the first Git change-recording tool and reserved locations for future knowledge maintenance.

## Source of truth

Git history and the project files are authoritative. The `Knowledge/` vault is derived project context for people and tools; it is not the source of truth.

Change records are generated from observable Git/project state rather than relying solely on an AI's self-report. The analyzer does not use an AI model and does not interpret the meaning of project changes.

## Change analyzer

`.ai/change_analyzer.py` is a standalone Python 3 script using only the standard library. It invokes Git to record commit details, changed paths and statuses, file categories, affected project areas, and diff statistics.

### Manual mode

Compare two specified commits:

    python .ai/change_analyzer.py <old_commit> <new_commit>

This creates one Markdown record for the new commit. Manual mode does not change the automatic-mode state.

### Automatic mode

Run without commit arguments:

    python .ai/change_analyzer.py

The analyzer reads HEAD and `.ai/state/last_processed_commit`. It creates a separate record for each commit after the saved commit, comparing each commit with its first parent. On the first run, if the state file is absent, it uses HEAD's parent when one exists. If HEAD has no parent, it does not invent one and leaves state unchanged.

After all required records are present, automatic mode advances the state file to HEAD. If HEAD already matches the saved SHA, it reports that there is nothing new to analyze. State must contain only a full lowercase commit SHA followed by a newline; it is persistent automation state, not project knowledge.

### Change records

`.ai/changes/` holds Markdown records named with the full SHA of the commit being recorded. Existing records are never replaced. Automatic mode may reuse an existing record only when its contents exactly match the record it would generate; a different or unreadable record stops analysis without advancing state.

## Change inspector

`.ai/change_inspector.py` deterministically inspects Git's actual old and new file contents for structural facts. It does not use an AI model, infer runtime behavior, or modify the Godot project or `Knowledge/`.

Run it manually for a commit range:

    python .ai/change_inspector.py <old_commit> <new_commit>

Or run automatic mode:

    python .ai/change_inspector.py

Manual mode writes one report for the selected new commit. Automatic mode reads `.ai/state/last_processed_commit` as the analyzer checkpoint, checks existing analyzer change records for missing inspection reports, and writes each pending report separately. It does not advance or otherwise modify shared state. When every existing analyzer record already has an inspection report, it prints `Nothing new to inspect.`

Reports are written to `.ai/changes/<full_commit_sha>-inspection.md`. Existing inspection reports are never overwritten. Supported textual structures include GDScript (`.gd`), scenes (`.tscn`), text resources (`.tres`), text-encoded resources (`.res`), and `project.godot`. Binary or unsupported files are identified without claiming to understand their internal structure. The inspection is a deterministic text analysis, not a complete Godot parser. It does not replace a future NotebookLM review or update `Knowledge/`.

## Reserved directories

- `.ai/reviews/` is reserved for knowledge-review results from future automated review processes.

## NotebookLM integration

`.ai/notebooklm_integration.py` connects the analyzer/inspector outputs to the installed `notebooklm-py` CLI. It performs deterministic file synchronization and submits inspection reports for NotebookLM review. It does not modify Godot files or `Knowledge/`, and it does not install packages.

The integration invokes the installed CLI as `python -m notebooklm`, so the package must be installed for the same Python interpreter used below. Authentication stays in the user's existing NotebookLM CLI profile outside this repository. The integration never reads, copies, or stores credentials, cookies, tokens, or browser profile data.

Check CLI availability and authentication, and list notebooks visible to the authenticated account:

    python .ai/notebooklm_integration.py check
    python .ai/notebooklm_integration.py list-notebooks

Select a notebook by its exact ID, or explicitly create a new project notebook:

    python .ai/notebooklm_integration.py configure <notebook_id>
    python .ai/notebooklm_integration.py create "Project Knowledge - <repository>"

The integration does not silently choose the active or first notebook. Notebook metadata is stored in `.ai/state/notebooklm_project.json`; it contains only the repository/project name, notebook ID/title, and configuration time.

Synchronize Markdown files under `Knowledge/` (excluding `Knowledge/.obsidian/`):

    python .ai/notebooklm_integration.py sync
    python .ai/notebooklm_integration.py sync --dry-run

The installed 0.8.3 CLI supports local-file source add and source delete, while refresh is for URL/Drive sources and append adds text rather than replacing it. Accordingly, changed tracked Markdown sources are re-synchronized by deleting only their tracked NotebookLM source and then uploading the current file. Deleted local Knowledge files remove only a corresponding source ID tracked by this integration. Each source is identified by repository-relative path, NotebookLM source ID, and SHA-256 in `.ai/state/notebooklm_sources.json`; source titles include the content hash to prevent accidental duplicate adds. A pre-existing same-title source not tracked in state causes a safe error for manual reconciliation.

Review one report or all pending reports:

    python .ai/notebooklm_integration.py review <commit_sha>
    python .ai/notebooklm_integration.py review-pending
    python .ai/notebooklm_integration.py review-pending --dry-run
    python .ai/notebooklm_integration.py

Reviews are saved to `.ai/reviews/<commit_sha>-review.md`. Existing reviews are not replaced unless `--overwrite` is supplied to `review` or `review-pending`. `.ai/state/notebooklm_reviews.json` tracks report hashes already reviewed, preventing repeated review requests. The no-argument mode synchronizes Knowledge, then processes pending inspection reports. It never changes `.ai/state/last_processed_commit`, which remains owned by the Change Analyzer.

Review publication uses an atomic state journal: the integration stages the returned text, records a `prepared` entry, publishes the review file without clobbering an existing file, then marks the entry `completed`. If interrupted, resume locally with:

    python .ai/notebooklm_integration.py reconcile <commit_sha>

`reconcile` does not contact NotebookLM. It can also repair state for an already-existing valid review when the report, configured notebook, and required review headings match. If a review file exists without matching state, ordinary review commands continue to refuse to overwrite it.

NotebookLM review is analysis only. The prompt distinguishes authoritative project files, derived Knowledge, deterministic inspection reports, and AI interpretation. NotebookLM does not directly update the vault; review suggestions must be applied separately after human/agent verification. CLI requests that fail do not mark a review as processed. The CLI's `ask --new` deletes the notebook's existing server-side conversation in version 0.8.3, so this integration avoids that destructive option and asks NotebookLM to treat each review as standalone within the existing conversation context.

NotebookLM must be authenticated and a project notebook explicitly configured before upload or review commands can make remote changes. The CLI/API remains an external service with its own availability and upload limits; this integration does not claim a review succeeded unless a valid response with the required headings was returned and saved.

## Knowledge Updater (proposal-only)

`.ai/knowledge_updater.py` reads completed NotebookLM reviews and validates recommendations against Git/project files and the current `Knowledge/` vault. Git and project files remain authoritative; NotebookLM review text is analysis and recommendation. The updater uses deterministic checks and does not apply Knowledge changes or modify the vault.

Generate a proposal for one completed review:

    python .ai/knowledge_updater.py <commit_sha>

Discover completed reviews that do not yet have a proposal:

    python .ai/knowledge_updater.py

Preview candidate commits and statuses without creating proposal files:

    python .ai/knowledge_updater.py --dry-run

Proposals are written to `.ai/proposals/<full_commit_sha>-knowledge-update.md`. Existing proposals are never overwritten. A review conclusion is marked `NO_UPDATE_REQUIRED` only when the updater can verify the required project and vault facts; unsupported or uncertain recommendations are marked `REQUIRES_REVIEW`. `UPDATE_PROPOSED` is reserved for concrete recommendations that can be validated against project evidence. Proposal files are published atomically. Applying an approved proposal is not implemented.

The updater reads the existing NotebookLM review state and review/inspection artifacts. It does not contact NotebookLM, store credentials, modify `.ai/state/`, change Git history, or edit Godot or Knowledge files.
