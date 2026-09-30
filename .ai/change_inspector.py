#!/usr/bin/env python3
"""Deterministically inspect structural facts in Git changes to Godot files."""

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path


class InspectorError(Exception):
    """An actionable Git, state, or filesystem error."""


def git(args, root, check=True):
    try:
        result = subprocess.run(
            ["git"] + list(args), cwd=str(root), stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, check=False,
        )
    except OSError as exc:
        raise InspectorError("Unable to run Git: {}".format(exc))
    if check and result.returncode:
        raise InspectorError(
            result.stderr.decode("utf-8", "replace").strip() or "Git command failed."
        )
    return result


def find_root():
    script_dir = Path(__file__).resolve().parent
    result = git(["rev-parse", "--show-toplevel"], script_dir)
    return Path(os.fsdecode(result.stdout.strip())).resolve()


def resolve(ref, root):
    result = git(["rev-parse", "--verify", "--end-of-options", ref + "^{commit}"], root)
    return result.stdout.decode("ascii").strip()


def commit_info(sha, root):
    raw = git(["show", "-s", "--format=%H%x00%P%x00%an%x00%aI%x00%s", sha], root).stdout
    fields = raw.rstrip(b"\n").split(b"\x00", 4)
    if len(fields) != 5:
        raise InspectorError("Incomplete commit metadata for {}.".format(sha))
    return {
        "sha": fields[0].decode("ascii"),
        "parents": fields[1].decode("ascii").split(),
        "author": fields[2].decode("utf-8", "replace"),
        "date": fields[3].decode("utf-8", "replace"),
        "subject": fields[4].decode("utf-8", "replace"),
    }


def changed_files(old_sha, new_sha, root):
    raw = git(["diff", "--name-status", "-z", "--find-renames", "--find-copies",
               old_sha, new_sha], root).stdout
    fields = raw.split(b"\0")
    if fields and not fields[-1]:
        fields.pop()
    files = []
    i = 0
    while i < len(fields):
        status = fields[i].decode("ascii", "replace")
        i += 1
        if status.startswith(("R", "C")):
            if i + 1 >= len(fields):
                raise InspectorError("Malformed Git rename/copy status.")
            old_path = fields[i].decode("utf-8", "surrogateescape")
            new_path = fields[i + 1].decode("utf-8", "surrogateescape")
            i += 2
            files.append(("Renamed" if status[0] == "R" else "Copied", old_path, new_path))
        else:
            if i >= len(fields):
                raise InspectorError("Malformed Git file status.")
            path = fields[i].decode("utf-8", "surrogateescape")
            i += 1
            label = {"A": "Added", "M": "Modified", "D": "Deleted"}.get(status[0], "Changed")
            files.append((label, path if label != "Added" else None,
                          path if label != "Deleted" else None))
    return files


def blob(revision, path, root):
    if revision is None:
        return None
    spec = revision + ":" + path
    result = git(["show", spec], root, check=False)
    if result.returncode:
        raise InspectorError("Could not read Git blob {}: {}".format(
            spec, result.stderr.decode("utf-8", "replace").strip()
        ))
    return result.stdout


def text_contents(raw):
    if raw is None:
        return None
    if b"\0" in raw:
        return None
    return raw.decode("utf-8", "replace")


def status_path_detail(kind, old_path, new_path):
    if kind == "Renamed":
        return "{} (from {})".format(new_path, old_path)
    return new_path or old_path


def inspect_gd(text):
    facts = {
        "extends": [], "classes": [], "signals": [], "exports": [],
        "onready": [], "globals": [], "functions": [], "resources": [],
        "nodes": [], "global_style": [], "class_types": [],
    }
    if text is None:
        return facts
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(line) - len(line.lstrip(" \t"))
        if indent == 0:
            for key, pattern in (
                ("extends", r"^extends\s+(.+?)\s*(?:#.*)?$"),
                ("classes", r"^class_name\s+([A-Za-z_][A-Za-z0-9_]*)"),
                ("signals", r"^(signal\s+[A-Za-z_][A-Za-z0-9_]*(?:\s*\([^)]*\))?)"),
                ("functions", r"^func\s+([A-Za-z_][A-Za-z0-9_]*)\s*\([^)]*\)\s*(?:->\s*[^:]+)?\s*:"),
            ):
                match = re.match(pattern, stripped)
                if match:
                    if key == "functions":
                        signature = stripped[:stripped.find(":") + 1]
                        facts[key].append(signature)
                    else:
                        facts[key].append(match.group(1))
            if re.match(r"^(?:@export\b|@export_)", stripped):
                facts["exports"].append(stripped)
            if re.match(r"^@onready\s+var\b", stripped):
                match = re.match(r"^@onready\s+var\s+([A-Za-z_]\w*)", stripped)
                if match:
                    facts["onready"].append((match.group(1), stripped))
            elif re.match(r"^(?:var|const)\s+[A-Za-z_]\w*", stripped):
                facts["globals"].append(stripped)
        for match in re.finditer(r"\b(?:preload|load)\s*\(\s*['\"](res://[^'\"]+)['\"]", line):
            facts["resources"].append(match.group(1))
        for match in re.finditer(r"\$[A-Za-z0-9_./:%]+|%[A-Za-z_][A-Za-z0-9_]*", line):
            facts["nodes"].append(match.group(0))
        for match in re.finditer(r"\bget_node(?:_or_null)?\s*\(\s*['\"]([^'\"]+)['\"]", line):
            facts["nodes"].append(match.group(1))
        for match in re.finditer(r"\b([A-Z][A-Za-z0-9_]*)\.([A-Za-z_]\w*)\b", line):
            facts["global_style"].append(match.group(1) + "." + match.group(2))
    for key in facts:
        if key in ("onready",):
            facts[key] = sorted(set(facts[key]))
        else:
            facts[key] = sorted(set(facts[key]))
    return facts


def attrs(section):
    return dict(re.findall(r'([A-Za-z_][A-Za-z0-9_]*)="([^"]*)"', section))


def inspect_scene(text):
    result = {"header": None, "nodes": [], "external": [], "subresources": [], "connections": [], "scripts": [], "instances": []}
    if text is None:
        return result
    current_node = None
    ext_paths = {}
    node_script_ids = []
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("[gd_scene "):
            result["header"] = attrs(line)
        elif line.startswith("[ext_resource "):
            a = attrs(line)
            result["external"].append(a)
            if "id" in a:
                ext_paths[a["id"]] = a.get("path", "")
        elif line.startswith("[sub_resource "):
            result["subresources"].append(attrs(line))
        elif line.startswith("[node "):
            a = attrs(line)
            current_node = a
            result["nodes"].append(a)
            instance = re.search(r'ExtResource\("([^"]+)"\)', a.get("instance", ""))
            if instance:
                result["instances"].append((a.get("name", "?"), instance.group(1)))
        elif line.startswith("[connection "):
            result["connections"].append(attrs(line))
        elif current_node is not None and line.startswith("script = ExtResource("):
            match = re.search(r'ExtResource\("([^"]+)"\)', line)
            if match:
                node_script_ids.append((current_node.get("name", "?"), match.group(1)))
    result["scripts"] = [(node, ext_paths.get(resource_id, "")) for node, resource_id in node_script_ids]
    result["instances"] = [(node, ext_paths.get(resource_id, "")) for node, resource_id in result["instances"]]
    return result


def inspect_project(text):
    data = {}
    section = ""
    if text is None:
        return data
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith(";") or stripped.startswith("#"):
            continue
        if stripped.startswith("[") and stripped.endswith("]"):
            section = stripped[1:-1]
        elif "=" in stripped:
            key, value = stripped.split("=", 1)
            data[(section, key.strip())] = value.strip()
    return data


def inspect_resource(text):
    result = {"header": None, "external": [], "subresources": [], "properties": [], "paths": []}
    if text is None:
        return result
    current_sub = None
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("[gd_resource "):
            result["header"] = attrs(stripped)
            current_sub = None
        elif stripped.startswith("[ext_resource "):
            result["external"].append(attrs(stripped))
            current_sub = None
        elif stripped.startswith("[sub_resource "):
            current_sub = attrs(stripped)
            result["subresources"].append(current_sub)
        elif stripped.startswith("["):
            current_sub = None
        elif "=" in stripped and not stripped.startswith(";"):
            result["properties"].append(stripped.split("=", 1)[0].strip())
        result["paths"].extend(re.findall(r"res://[^\s\"']+", stripped))
    result["properties"] = sorted(set(result["properties"]))
    result["paths"] = sorted(set(result["paths"]))
    return result


def path_kind(path):
    low = path.lower()
    if low.endswith(".gd"):
        return "gd"
    if low.endswith(".tscn"):
        return "scene"
    if low.endswith((".tres", ".res")):
        return "resource"
    if low.replace("\\", "/").rsplit("/", 1)[-1] == "project.godot":
        return "project"
    return None


def md(value):
    return "`{}`".format(str(value).replace("`", "\\`"))


def section_list(lines, title, values, formatter=None):
    if not values:
        return
    lines.extend([title, ""])
    for item in values:
        lines.append("- " + (formatter(item) if formatter else md(item)))
    lines.append("")


def diff_values(before, after):
    before, after = set(before), set(after)
    return sorted(after - before), sorted(before - after)


def compare_simple(lines, title, before, after):
    added, removed = diff_values(before, after)
    if added or removed:
        lines.extend([title, ""])
        if added:
            lines.append("Added:")
            lines.extend("- " + md(x) for x in added)
        if removed:
            lines.append("Removed:")
            lines.extend("- " + md(x) for x in removed)
        lines.append("")


def scene_nodes_lines(scene):
    nodes = scene["nodes"]
    if not nodes:
        return ["No node declarations detected."]
    by_parent = {}
    for node in nodes:
        by_parent.setdefault(node.get("parent", "."), []).append(node)
    output = []
    emitted = set()

    def visit(parent, depth):
        for node in by_parent.get(parent, []):
            key = (node.get("name"), node.get("parent"), node.get("type"))
            if key in emitted:
                continue
            emitted.add(key)
            output.append("  " * depth + "- {} ({})".format(node.get("name", "?"), node.get("type", "?")))
            visit(node.get("name", ""), depth + 1)

    roots = [n for n in nodes if n.get("parent") in (None, ".")]
    for node in roots:
        visit(node.get("parent", "."), 0)
        break
    for node in nodes:
        key = (node.get("name"), node.get("parent"), node.get("type"))
        if key not in emitted:
            output.append("- {} ({}) [parent: {}]".format(node.get("name", "?"), node.get("type", "?"), node.get("parent", "?")))
    return output


def inspect_one_file(lines, kind, status, old_path, new_path, old_sha, new_sha, root):
    path = new_path or old_path
    lines.extend(["### {}".format(path), "", "Status: {}".format(status), ""])
    before = text_contents(blob(old_sha, old_path, root)) if old_path else None
    after = text_contents(blob(new_sha, new_path, root)) if new_path else None
    if kind is None:
        lines.extend(["Structural inspection: unsupported file type; the Git change is recorded without interpreting its contents.", ""])
        return
    if (old_path and before is None and status in ("Modified", "Deleted", "Renamed", "Copied")) or (new_path and after is None and status not in ("Deleted",)):
        lines.extend(["Structural inspection: contents are binary, unavailable, or not safely decodable as text.", ""])
        return
    if kind == "gd":
        a, b = inspect_gd(before), inspect_gd(after)
        if status == "Modified" or status == "Renamed":
            lines.extend(["#### Structural Changes", ""])
            compare_simple(lines, "Functions", a["functions"], b["functions"])
            compare_simple(lines, "Signals", a["signals"], b["signals"])
            compare_simple(lines, "Export declarations", a["exports"], b["exports"])
            compare_simple(lines, "Extends declarations", a["extends"], b["extends"])
            compare_simple(lines, "Class names", a["classes"], b["classes"])
            compare_simple(lines, "Top-level variables/constants", a["globals"], b["globals"])
            compare_simple(lines, "Onready declarations", [repr(x) for x in a["onready"]], [repr(x) for x in b["onready"]])
            compare_simple(lines, "Node references", a["nodes"], b["nodes"])
            compare_simple(lines, "Resource dependencies", a["resources"], b["resources"])
        lines.extend(["#### Observed Structure", ""])
        if after is None:
            lines.append("No new version (file deleted).")
        else:
            for label, key in (("Extends", "extends"), ("Class name", "classes"), ("Signals", "signals"),
                               ("Export declarations", "exports"), ("Onready variables", "onready"),
                               ("Top-level variables/constants", "globals"), ("Functions", "functions"),
                               ("Node references", "nodes"), ("Resource dependencies", "resources"),
                               ("Detected global-style references", "global_style")):
                section_list(lines, label + ":", b[key], lambda x: md(x[0] + " — " + x[1]) if isinstance(x, tuple) else md(x))
        lines.append("")
    elif kind == "scene":
        a, b = inspect_scene(before), inspect_scene(after)
        if status in ("Modified", "Renamed"):
            lines.extend(["#### Structural Changes", ""])
            old_nodes = {(n.get("name"), n.get("type"), n.get("parent")) for n in a["nodes"]}
            new_nodes = {(n.get("name"), n.get("type"), n.get("parent")) for n in b["nodes"]}
            add, rem = sorted(new_nodes - old_nodes), sorted(old_nodes - new_nodes)
            if add:
                section_list(lines, "Added nodes:", add, lambda x: md("{} ({}) under {}".format(x[0], x[1], x[2])))
            if rem:
                section_list(lines, "Removed nodes:", rem, lambda x: md("{} ({}) under {}".format(x[0], x[1], x[2])))
            compare_simple(lines, "External resources", [repr(sorted(x.items())) for x in a["external"]], [repr(sorted(x.items())) for x in b["external"]])
            compare_simple(lines, "Signal connections", ["{}.{} → {}.{}".format(x.get("from", "?"), x.get("signal", "?"), x.get("to", "?"), x.get("method", "?")) for x in a["connections"]], ["{}.{} → {}.{}".format(x.get("from", "?"), x.get("signal", "?"), x.get("to", "?"), x.get("method", "?")) for x in b["connections"]])
        lines.extend(["#### Scene Structure", ""])
        lines.extend(scene_nodes_lines(b) if after is not None else ["No new version (scene deleted)."])
        lines.append("")
        section_list(lines, "#### Scripts", ["{} → {}".format(n, p) for n, p in b["scripts"]])
        section_list(lines, "#### Instanced Scenes", ["{} → {}".format(n, p) for n, p in b["instances"]])
        section_list(lines, "#### External Resources", ["{} {} ({})".format(x.get("type", "?"), x.get("path", "?"), x.get("id", "?")) for x in b["external"]])
        section_list(lines, "#### Subresources", ["{} ({})".format(x.get("type", "?"), x.get("id", "?")) for x in b["subresources"]])
        section_list(lines, "#### Signal Connections", ["{}.{} → {}.{}".format(x.get("from", "?"), x.get("signal", "?"), x.get("to", "?"), x.get("method", "?")) for x in b["connections"]])
    elif kind == "project":
        a, b = inspect_project(before), inspect_project(after)
        lines.extend(["#### Project Configuration", ""])
        if after is None:
            lines.append("No new version (configuration deleted).")
        else:
            changed_keys = sorted(set(a) | set(b))
            relevant = []
            for sect, key in changed_keys:
                if a.get((sect, key)) != b.get((sect, key)) and (
                    (sect == "application" and key == "run/main_scene")
                    or sect == "autoload" or sect == "input"
                    or sect.startswith(("input/", "display/", "rendering/"))
                ):
                    relevant.append((sect, key, a.get((sect, key)), b.get((sect, key))))
            if relevant:
                for sect, key, old_value, new_value in relevant:
                    lines.append("- [{}] {}: {} → {}".format(
                        sect, key, md(old_value if old_value is not None else "(absent)"),
                        md(new_value if new_value is not None else "(absent)")))
            else:
                lines.append("No targeted configuration changes detected.")
            autoloads = [(key, value) for (sect, key), value in sorted(b.items()) if sect == "autoload"]
            if autoloads:
                lines.extend(["", "Detected autoloads:"])
                lines.extend("- {} → {}".format(md(key), md(value)) for key, value in autoloads)
        lines.append("")
    else:
        resource = inspect_resource(after if after is not None else before)
        lines.extend(["#### Resource Structure", ""])
        if resource["header"]:
            lines.append("- Resource header: {}".format(md(resource["header"])))
        else:
            lines.append("- No text resource header detected; arbitrary/binary data was not interpreted.")
        section_list(lines, "External resources:", ["{} {} ({})".format(x.get("type", "?"), x.get("path", "?"), x.get("id", "?")) for x in resource["external"]])
        section_list(lines, "Subresources:", ["{} ({})".format(x.get("type", "?"), x.get("id", "?")) for x in resource["subresources"]])
        section_list(lines, "Property names:", resource["properties"])
        section_list(lines, "Referenced res:// paths:", resource["paths"])


def inspection_record(new_sha, old_sha, root):
    info = commit_info(new_sha, root)
    paths = changed_files(old_sha, new_sha, root)
    lines = ["# Change Inspection", "", "## Commit", "",
             "- SHA: {}".format(md(info["sha"])),
             "- Parent: {}".format(md(info["parents"][0] if info["parents"] else "(none)")),
             "- Compared from: {}".format(md(old_sha)),
             "- Author: {}".format(md(info["author"])),
             "- Date: {}".format(md(info["date"])),
             "- Subject: {}".format(md(info["subject"])), "", "## Files Inspected", ""]
    if not paths:
        lines.extend(["No changed paths between the selected commits.", ""])
    for status, old_path, new_path in paths:
        path = new_path or old_path
        inspect_one_file(lines, path_kind(path), status, old_path, new_path, old_sha, new_sha, root)
    lines.extend(["## Project-Level Changes", ""])
    relevant = [status_path_detail(*entry) for entry in paths if path_kind(entry[2] or entry[1]) == "project"]
    if relevant:
        lines.extend("- {}".format(md(x)) for x in relevant)
    else:
        lines.append("No project.godot changes detected in this commit range.")
    lines.extend(["", "## Limitations", "",
                  "This inspection uses deterministic textual analysis of Git contents. It is not a full GDScript or Godot parser and does not infer runtime behavior. Unsupported file types are listed without interpreting their structure.", ""])
    return "\n".join(lines)


def record_commits(root):
    directory = root / ".ai" / "changes"
    entries = []
    for path in sorted(directory.glob("*.md")):
        match = re.fullmatch(r"([0-9a-f]{40}|[0-9a-f]{64})\.md", path.name)
        if match:
            entries.append((match.group(1), path))
    return entries


def inspection_path(root, sha):
    return root / ".ai" / "changes" / (sha + "-inspection.md")


def write_new(path, contents):
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("xb") as out:
            out.write(contents.encode("utf-8", "backslashreplace"))
    except FileExistsError:
        raise InspectorError("Inspection record already exists; refusing to overwrite: {}".format(path))


def inspect_range(old_ref, new_ref, root):
    old_sha, new_sha = resolve(old_ref, root), resolve(new_ref, root)
    target = inspection_path(root, new_sha)
    if target.exists():
        raise InspectorError("Inspection record already exists; refusing to overwrite: {}".format(target.relative_to(root)))
    report = inspection_record(new_sha, old_sha, root)
    write_new(target, report)
    print("Wrote {}".format(target.relative_to(root).as_posix()))


def automatic(root):
    state_path = root / ".ai" / "state" / "last_processed_commit"
    try:
        raw = state_path.read_bytes()
    except OSError as exc:
        raise InspectorError("Cannot read shared analyzer state: {}".format(exc))
    match = re.fullmatch(rb"([0-9a-f]{40}|[0-9a-f]{64})\r?\n", raw)
    if not match:
        raise InspectorError("Shared state must contain one full commit SHA followed by a newline.")
    checkpoint = resolve(match.group(1).decode("ascii"), root)
    head = resolve("HEAD", root)
    ancestor = git(["merge-base", "--is-ancestor", checkpoint, head], root, check=False)
    if ancestor.returncode != 0:
        raise InspectorError("Shared analyzer state is not an ancestor of HEAD; no inspections were written.")

    # Analyzer records are the durable queue: state may already equal HEAD because
    # the analyzer runs before this inspector. Missing inspection files are pending.
    pending = []
    for sha, source_record in record_commits(root):
        resolve(sha, root)
        in_head = git(["merge-base", "--is-ancestor", sha, head], root, check=False)
        if in_head.returncode != 0:
            raise InspectorError("Analyzer record {} is not on current HEAD history; refusing ambiguous automatic inspection.".format(sha))
        target = inspection_path(root, sha)
        if not target.exists():
            info = commit_info(sha, root)
            parents = info["parents"]
            if not parents:
                raise InspectorError("Pending analyzer record is for a root commit without a comparable parent: {}".format(sha))
            pending.append((sha, parents[0], target))
    if not pending:
        print("Nothing new to inspect.")
        return

    # Build all reports before writing any, so analysis failures do not partially
    # advance or otherwise alter shared processing state.
    prepared = [(target, inspection_record(sha, parent, root)) for sha, parent, target in pending]
    existing = [target for target, _ in prepared if target.exists()]
    if existing:
        raise InspectorError("Inspection output appeared during analysis; refusing to overwrite {}".format(existing[0]))
    for target, report in prepared:
        write_new(target, report)
        print("Wrote {}".format(target.relative_to(root).as_posix()))


def main(argv=None):
    parser = argparse.ArgumentParser(description="Inspect structural facts in Git changes.")
    parser.add_argument("old_commit", nargs="?", help="previous commit")
    parser.add_argument("new_commit", nargs="?", help="new commit")
    args = parser.parse_args(argv)
    if (args.old_commit is None) != (args.new_commit is None):
        parser.error("supply both commit references or neither for automatic mode")
    try:
        root = find_root()
        if args.old_commit is None:
            automatic(root)
        else:
            inspect_range(args.old_commit, args.new_commit, root)
        return 0
    except (InspectorError, OSError) as exc:
        print("change_inspector: {}".format(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
