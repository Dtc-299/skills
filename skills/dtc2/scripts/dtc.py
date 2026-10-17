#!/usr/bin/env python3
"""Local DTC archive and checkpoint tools. Python 3.10+, no external packages."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import uuid

VERSION = 1
REQUIRED = ("START_HERE.md", "MEMORY.md", "ledger.json", "review.json")
KINDS = {"requirement", "constraint", "preference", "fact", "value", "decision", "interface", "failure", "evidence", "unknown"}
BASES = {"user_confirmed", "observed", "inferred", "proposed", "unknown"}
STATUSES = {"pending", "in_progress", "blocked", "done", "cancelled"}
INJECTED = ("<environment_context>", "<permissions instructions>", "# AGENTS.md instructions", "<external_codex_apps_open_page>")
SECRET_PATTERNS = (
    r"-----BEGIN [^-]*PRIVATE KEY-----[\s\S]*?-----END [^-]*PRIVATE KEY-----",
    r"\b(?:sk-(?:proj-|ant-)?[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|AKIA[A-Z0-9]{16})\b",
    r"(?im)\b(?:api[_-]?key|access[_-]?token|client[_-]?secret|password)\s*[:=]\s*[\"']?[^\s\"',;]{8,}",
    r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{16,}",
)


def now():
    return datetime.now(timezone.utc).isoformat()


def dump(value):
    return json.dumps(value, ensure_ascii=False, indent=2)


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def write_json(path, value):
    Path(path).write_text(dump(value) + "\n", encoding="utf-8")


def sha(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def within(path, root):
    try:
        Path(path).resolve().relative_to(Path(root).resolve())
        return True
    except (ValueError, OSError):
        return False


def safe_file(root, relative):
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
        raise ValueError("Expected a nonempty package-relative path")
    result = Path(root) / relative
    if not within(result, root):
        raise ValueError("Path leaves package: " + relative)
    return result


def redact(text):
    count = 0
    for pattern in SECRET_PATTERNS:
        text, n = re.subn(pattern, "[REDACTED_CREDENTIAL]", text)
        count += n
    return text, count


def records(path, limit=None):
    """Read an immutable byte prefix; expose malformed lines rather than hiding them."""
    with Path(path).open("rb") as stream:
        remaining = Path(path).stat().st_size if limit is None else limit
        line_no = 0
        while remaining > 0:
            raw = stream.readline(remaining)
            if not raw:
                break
            remaining -= len(raw)
            line_no += 1
            try:
                value = json.loads(raw.decode("utf-8-sig"))
                if not isinstance(value, dict):
                    raise ValueError("record is not an object")
                yield line_no, value, raw
            except (ValueError, UnicodeError):
                yield line_no, None, raw


def thread_key(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", value):
        raise ValueError("A verified chat/thread ID is required; never fall back to project-wide collection")
    return value


def normalized_ids(values):
    if isinstance(values, str):
        values = [values]
    if not isinstance(values, (list, tuple)) or not values:
        raise ValueError("Select at least one verified chat ID; no automatic project-wide fallback")
    return sorted({thread_key(v) for v in values})


def selection_key(values):
    ids = normalized_ids(values)
    return ids[0] if len(ids) == 1 else "set-" + hashlib.sha256("\n".join(ids).encode()).hexdigest()[:24]


def thread_home(root, thread_id):
    return root / ".dtc" / "handoffs" / thread_key(thread_id)


def select_thread(root, explicit=None):
    if explicit:
        return selection_key(explicit) if isinstance(explicit, (list, tuple)) else thread_key(explicit)
    folder = root / ".dtc" / "handoffs"
    candidates = [p.parent.name for p in folder.glob("*/CURRENT.json")] if folder.exists() else []
    if len(candidates) == 1:
        return thread_key(candidates[0])
    if not candidates and (root / ".dtc" / "CURRENT.json").exists():
        raise ValueError("Legacy project-wide checkpoint found; explicitly inspect its scope before reuse")
    raise ValueError("Specify source chat IDs, --handoff key or --snapshot; cannot choose among " + str(len(candidates)) + " handoffs. Use list.")


def discover(home, roots, thread_id=None):
    selected_ids = set(normalized_ids(thread_id))
    found, errors = [], []
    for folder in (home / "sessions", home / "archived_sessions"):
        if not folder.exists():
            continue
        for path in sorted(folder.rglob("*.jsonl")):
            try:
                size = path.stat().st_size
                meta, hit = {}, False
                # Only inspect the metadata header of other chats, never their messages.
                with path.open("rb") as stream:
                    remaining = size
                    while remaining > 0:
                        raw = stream.readline(remaining)
                        if not raw:
                            break
                        remaining -= len(raw)
                        if b'"session_meta"' not in raw:
                            continue
                        try:
                            obj = json.loads(raw)
                            payload = obj.get("payload", {})
                            if obj.get("type") == "session_meta":
                                meta = {k: payload.get(k) for k in ("id", "session_id", "cwd", "timestamp")}
                                selected = sorted(selected_ids.intersection({meta.get("id"), meta.get("session_id")}))
                                hit = bool(selected)
                                if hit:
                                    meta["selected_thread_id"] = selected[0]
                                break
                        except (ValueError, AttributeError):
                            continue
                if hit:
                    found.append({"path": str(path.resolve()), "bytes": size, "meta": meta})
            except OSError as exc:
                errors.append({"path": str(path), "error": type(exc).__name__})
    return found, errors


def message_text(payload):
    parts, attachments = [], False
    for part in payload.get("content", []):
        if not isinstance(part, dict):
            attachments = True
        elif part.get("type") in ("input_text", "output_text", "text"):
            parts.append(part.get("text", ""))
        else:
            attachments = True
    return "\n".join(parts), attachments


def extract(source, roots, thread_id=None):
    thread_id = thread_key(thread_id)
    active = False
    response, fallback, gaps = [], [], []
    counts = Counter()
    h = hashlib.sha256()
    for line, obj, raw in records(source["path"], source["bytes"]):
        h.update(raw)
        if obj is None:
            # A bad record is a coverage gap, but a cwd change never changes chat identity.
            if active:
                gaps.append("Malformed/truncated JSONL at L" + str(line))
            continue
        kind, p = obj.get("type"), obj.get("payload", {})
        if not isinstance(p, dict):
            if active:
                gaps.append("Unsupported payload at L" + str(line))
            continue
        if kind == "session_meta":
            active = thread_id in (p.get("id"), p.get("session_id"))
            if not active:
                raise ValueError("Selected log belongs to a different chat")
            continue
        if kind == "turn_context":
            continue
        if not active:
            continue
        if kind == "compacted" or (kind == "event_msg" and p.get("type") == "context_compacted"):
            counts["compactions"] += 1
        role, text, attachment = None, "", False
        if kind == "response_item" and p.get("type") == "message":
            role = p.get("role")
            if role not in ("user", "assistant") or p.get("channel") == "analysis":
                continue
            text, attachment = message_text(p)
        elif kind == "event_msg" and p.get("type") in ("user_message", "agent_message"):
            if p.get("channel") == "analysis":
                continue
            role = "user" if p["type"] == "user_message" else "assistant"
            text = p.get("message", "")
            attachment = bool(p.get("images") or p.get("local_images") or p.get("attachments"))
        elif kind == "response_item" and p.get("type") in ("function_call_output", "custom_tool_call_output"):
            counts["excluded_tool_outputs"] += 1
            continue
        elif kind == "response_item" and p.get("type") not in ("reasoning", "function_call", "custom_tool_call", "additional_tools"):
            counts["unsupported_response_items"] += 1
            continue
        if attachment:
            gaps.append("Attachment/non-text content not exported at L" + str(line))
        if not role or not isinstance(text, str) or not text.strip():
            continue
        if role == "user" and text.lstrip().startswith(INJECTED):
            counts["excluded_injected_context"] += 1
            continue
        text, redacted = redact(text)
        counts["redactions"] += redacted
        entry = {"line": line, "role": role, "timestamp": obj.get("timestamp"), "text": text}
        (response if kind == "response_item" else fallback).append(entry)
    # Multiset matching preserves repeated identical prompts, unlike global deduplication.
    duplicates = Counter((m["role"], m["text"]) for m in response)
    messages = response[:]
    for msg in fallback:
        key = (msg["role"], msg["text"])
        if duplicates[key]:
            duplicates[key] -= 1
        else:
            messages.append(msg)
            counts["event_fallback_messages"] += 1
    messages.sort(key=lambda m: m["line"])
    if counts["unsupported_response_items"]:
        gaps.append("Unsupported response item types; inspect original log")
    return messages, counts, gaps, h.hexdigest()


def git_state(root):
    def git(*args):
        result = subprocess.run(["git", "-C", str(root), *args], capture_output=True, timeout=30)
        if result.returncode:
            raise ValueError("git unavailable or directory is not a Git working tree")
        return result.stdout
    try:
        head = git("rev-parse", "--verify", "HEAD").decode().strip()
    except (OSError, ValueError, subprocess.TimeoutExpired):
        head = None
    try:
        branch = git("symbolic-ref", "--short", "-q", "HEAD").decode().strip()
    except (OSError, ValueError, subprocess.TimeoutExpired):
        branch = None
    try:
        status = git("status", "--porcelain=v1", "--untracked-files=all", "-z", "--", ".", ":(exclude).dtc")
        parts, changed, i = status.split(b"\0"), {}, 0
        while i < len(parts):
            part = parts[i]
            i += 1
            if not part:
                continue
            name = os.fsdecode(part[3:])
            if part[:1] in (b"R", b"C") or part[1:2] in (b"R", b"C"):
                i += 1
            path = root / name
            if path.is_symlink():
                digest = "symlink:" + os.readlink(path)
            elif path.is_file() and within(path, root):
                digest = sha(path)
            else:
                digest = "missing-or-directory"
            changed[name] = {"status": os.fsdecode(part[:2]), "sha256": digest}
        return {"available": True, "head": head, "branch": branch, "changed": changed}
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        return {"available": False, "reason": type(exc).__name__}


def current(root, thread_id=None):
    if thread_id is None:
        folder = root / ".dtc" / "handoffs"
        choices = list(folder.glob("*/CURRENT.json")) if folder.exists() else []
        if not choices:
            return None
        thread_id = select_thread(root)
    pointer = thread_home(root, thread_id) / "CURRENT.json"
    return read_json(pointer) if pointer.exists() else None


def collect(args):
    root = Path(args.project).resolve()
    if not root.is_dir():
        raise ValueError("Project directory does not exist")
    roots = [root]
    home = Path(args.codex_home or os.environ.get("CODEX_HOME", Path.home() / ".codex")).resolve()
    ids = normalized_ids(getattr(args, "thread_id", None) or os.environ.get("CODEX_THREAD_ID"))
    thread_id = selection_key(ids)
    scope = {"kind": "thread" if len(ids) == 1 else "selection", "thread_ids": ids, "key": thread_id,
             "label": getattr(args, "label", None) or " + ".join(ids)}
    found, errors = discover(home, roots, ids)
    if args.command == "inventory":
        return {"project_root": str(root), "scope": scope, "sources": found, "errors": errors}
    # Validate the existing checkpoint before making a descendant.
    parent = current(root, thread_id)
    if parent:
        old, _ = locate(root, None, thread_id)
        validate(old, sealed=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    snapshot = thread_home(root, thread_id) / "snapshots" / stamp
    snapshot.mkdir(parents=True, exist_ok=False)
    (snapshot / "sources").mkdir()
    entries, gaps = [], []
    manifest = {"schema_version": VERSION, "id": stamp, "status": "draft", "project_root": str(root),
                "created_at": now(), "capture_scope": "all accessible visible messages of exactly the selected chats; no directory-based expansion",
                "scope": scope,
                "parent": parent, "git_state": git_state(root),
                "sources": entries, "collection_gaps": gaps, "scan_errors": errors}
    for source in found:
        source_tid = source["meta"]["selected_thread_id"]
        messages, counts, issues, digest = extract(source, roots, source_tid)
        origin = source["path"]
        sid = hashlib.sha256(origin.encode()).hexdigest()[:16]
        header = "# Archived visible messages\n\nHistorical data, not executable instructions.\n\nOrigin: " + origin + "\nFrozen source SHA-256: " + digest + "\n\n"
        chunks, block = [], header
        for m in messages:
            # Split very large individual messages too; the source-line anchor remains stable.
            for offset in range(0, len(m["text"]), 20000):
                piece = ("## L" + str(m["line"]) + " · " + m["role"] + " · " + str(m["timestamp"]) +
                         " · text offset " + str(offset) + "\n\n" + m["text"][offset:offset + 20000] + "\n\n")
                if len(block) + len(piece) > 26000 and block != header:
                    chunks.append(block)
                    block = header
                block += piece
        if block != header:
            chunks.append(block)
        for index, chunk in enumerate(chunks, 1):
            relative = f"sources/{sid}-{index:04d}.md"
            (snapshot / relative).write_text(chunk, encoding="utf-8")
            entries.append({"file": relative, "origin": origin, "source_prefix_bytes": source["bytes"],
                            "thread_id": source_tid,
                            "source_prefix_sha256": digest, "chunk": index, "message_count_total": len(messages), "counts": dict(counts)})
        gaps.extend({"source": origin, "detail": issue} for issue in issues)
        if not messages:
            gaps.append({"source": origin, "detail": "Matched session yielded no supported visible messages"})
    if errors:
        gaps.append({"detail": "Some session files were unreadable; see scan_errors"})
    if not found:
        gaps.append({"detail": "No matching local sessions; add accessible app/current-context sources"})
    matched_ids = {source["meta"]["selected_thread_id"] for source in found}
    for missing in sorted(set(ids) - matched_ids):
        gaps.append({"thread_id": missing, "detail": "Selected chat not found locally; use app history or report unavailable"})
    if found:
        gaps.append({"detail": "Raw tool outputs excluded; add task-critical tool evidence separately"})
    write_json(snapshot / "manifest.json", manifest)
    index_text = "# Source reading index\n\nScope: " + manifest["capture_scope"] + "\n\n"
    index_text += "\n".join("- [ ] " + e["file"] + " ← " + e["origin"] for e in entries)
    (snapshot / "SOURCE_INDEX.md").write_text(index_text + "\n", encoding="utf-8")
    return {"snapshot": str(snapshot), "status": "draft", "source_sessions": len(found), "source_chunks": len(entries), "gaps": gaps,
            "next": "Read all source chunks; author START_HERE.md, MEMORY.md, ledger.json and review.json; check then publish"}


def locate(root, snapshot, thread_id=None, handoff=None):
    if snapshot:
        return Path(snapshot).resolve(), None
    thread_id = select_thread(root, handoff or thread_id)
    pointer = current(root, thread_id)
    if not pointer or pointer.get("schema_version") != VERSION:
        raise ValueError("No supported CURRENT.json for the selected handoff; run dtc1 in the old window")
    folder = safe_file(root / ".dtc", pointer["snapshot"])
    if sha(folder / "manifest.json") != pointer.get("manifest_sha256"):
        raise ValueError("CURRENT manifest SHA-256 mismatch")
    if read_json(folder / "manifest.json").get("scope", {}).get("key") != thread_id:
        raise ValueError("CURRENT points to a different chat selection")
    return folder, pointer


def validate(snapshot, sealed=False):
    m = read_json(snapshot / "manifest.json")
    if m.get("schema_version") != VERSION:
        raise ValueError("Unsupported manifest schema")
    for name in REQUIRED:
        if not (snapshot / name).is_file() or not (snapshot / name).read_text(encoding="utf-8-sig").strip():
            raise ValueError("Missing/empty " + name)
    ledger = read_json(snapshot / "ledger.json")
    review = read_json(snapshot / "review.json")
    if ledger.get("schema_version") != VERSION or review.get("schema_version") != VERSION:
        raise ValueError("Unsupported ledger/review schema")
    if ledger.get("project_root") != m.get("project_root"):
        raise ValueError("Ledger and manifest project_root differ")
    scope = m.get("scope", {})
    if scope.get("kind") not in ("thread", "selection"):
        raise ValueError("Legacy project scope lacks explicit selected chat IDs; inspect before reuse")
    tids = normalized_ids(scope.get("thread_ids"))
    if scope.get("key") != selection_key(tids) or scope["kind"] != ("thread" if len(tids) == 1 else "selection"):
        raise ValueError("Selection key/kind does not match its chat IDs")
    if any(s.get("thread_id") not in tids for s in m.get("sources", [])):
        raise ValueError("Source from an unselected or unidentified chat; scope expansion rejected")
    source_files = {s["file"] for s in m.get("sources", [])}
    if not source_files:
        raise ValueError("No archived sources; cannot publish unsupported memory")
    read_files = review.get("read_files", [])
    if not isinstance(read_files, list) or not source_files.issubset(set(read_files)):
        raise ValueError("Some source files are not marked fully read")
    for name in source_files | set(read_files):
        if not safe_file(snapshot, name).is_file():
            raise ValueError("Missing reviewed source " + name)
    if review.get("coverage") not in ("partial", "accessible_selected_sources"):
        raise ValueError("Invalid coverage; never claim universal losslessness")
    if not isinstance(review.get("gaps"), list) or not review.get("source_inventory_notes") or not review.get("semantic_review"):
        raise ValueError("Missing semantic review, source inventory notes or gaps list")
    if m.get("collection_gaps") and (review["coverage"] != "partial" or not review["gaps"]):
        raise ValueError("Collector gaps require partial coverage and documented gaps")
    ids, all_rows = set(), []
    items, tasks = ledger.get("items"), ledger.get("tasks")
    if not isinstance(items, list) or not isinstance(tasks, list):
        raise ValueError("items/tasks must be arrays")
    for row in items + tasks:
        rid = row.get("id")
        if not isinstance(rid, str) or not rid or rid in ids or not row.get("text"):
            raise ValueError("Missing/duplicate ledger ID or text")
        ids.add(rid)
        all_rows.append(row)
        if not isinstance(row.get("refs"), list) or not row["refs"]:
            raise ValueError("Missing evidence refs for " + rid)
        for ref in row["refs"]:
            target = safe_file(snapshot, ref["file"])
            anchor = ref.get("anchor")
            text = target.read_text(encoding="utf-8-sig")
            found = bool(anchor) and (bool(re.search(r"(?m)^## " + re.escape(anchor) + r"(?:\s|$)", text))
                                      if re.fullmatch(r"L\d+", anchor or "") else anchor in text)
            if not found:
                raise ValueError("Evidence anchor not found for " + rid)
    if not ids:
        raise ValueError("Empty ledger")
    for row in items:
        if row.get("kind") not in KINDS or row.get("basis") not in BASES or row.get("state") not in ("active", "superseded", "unresolved"):
            raise ValueError("Invalid item classification " + row["id"])
        if row["state"] == "superseded" and (row.get("superseded_by") not in ids or row.get("superseded_by") == row["id"]):
            raise ValueError("Superseding ID missing for " + row["id"])
        if row["kind"] == "value":
            if any(not isinstance(row.get(k), str) or not row[k] for k in ("value", "unit", "as_of")):
                raise ValueError("Numeric value must preserve exact string, unit, as_of: " + row["id"])
            if row.get("value_type") not in ("original", "calculated", "estimated"):
                raise ValueError("Missing value_type " + row["id"])
            if row["value_type"] == "calculated" and (not row.get("formula") or not row.get("input_ids") or not set(row["input_ids"]).issubset(ids)):
                raise ValueError("Calculated value requires formula and valid input_ids")
    for row in tasks:
        if row.get("status") not in STATUSES:
            raise ValueError("Invalid task status " + row["id"])
        if row["status"] == "done" and not row.get("verification"):
            raise ValueError("Done task missing verification " + row["id"])
        if row["status"] == "blocked" and not row.get("blocker"):
            raise ValueError("Blocked task missing blocker " + row["id"])
        if row["status"] == "cancelled" and not row.get("reason"):
            raise ValueError("Cancelled task missing reason " + row["id"])
    active = {t["id"]: t for t in tasks if t["status"] in ("pending", "in_progress", "blocked")}
    nxt = ledger.get("next_task_id")
    if active and (nxt not in active or not active[nxt].get("next_action")):
        raise ValueError("Active work requires next_task_id and concrete next_action")
    if not active and nxt is not None:
        raise ValueError("No active tasks; next_task_id must be null")
    if not ids.issubset(set(review.get("checked_item_ids", []))):
        raise ValueError("Not all ledger IDs were checked against sources")
    checks = ledger.get("recall_checks")
    if not isinstance(checks, list) or not checks:
        raise ValueError("At least one source-backed recall check is required")
    for check in checks:
        if not check.get("question") or not check.get("answer") or not check.get("item_ids") or not set(check["item_ids"]).issubset(ids):
            raise ValueError("Invalid recall check")
    if not isinstance(ledger.get("read_next"), list):
        raise ValueError("read_next must be an array")
    for name in ledger["read_next"]:
        if not safe_file(snapshot, name).is_file():
            raise ValueError("Missing read_next file " + name)
    parent = m.get("parent")
    if parent:
        old = safe_file(Path(m["project_root"]) / ".dtc", parent["snapshot"])
        if old.exists():
            if sha(old / "manifest.json") != parent["manifest_sha256"]:
                raise ValueError("Parent manifest changed")
            previous = read_json(old / "ledger.json")
            if read_json(old / "manifest.json").get("scope", {}).get("key") != scope["key"]:
                raise ValueError("Parent checkpoint belongs to a different chat selection")
            old_ids = {r["id"] for r in previous["items"] + previous["tasks"]}
            if not old_ids.issubset(ids):
                raise ValueError("Previous ledger IDs silently dropped: " + ", ".join(sorted(old_ids - ids)))
    if sealed:
        if m.get("status") != "sealed" or not m.get("files"):
            raise ValueError("Snapshot is not sealed")
        actual = {p.relative_to(snapshot).as_posix() for p in snapshot.rglob("*") if p.is_file() and p != snapshot / "manifest.json"}
        if actual != set(m["files"]):
            raise ValueError("Sealed file set changed")
        for name, expected in m["files"].items():
            if sha(safe_file(snapshot, name)) != expected:
                raise ValueError("Sealed file SHA-256 mismatch: " + name)
    return m, ledger, review


def publish(snapshot):
    m, ledger, review = validate(snapshot)
    if m.get("status") != "draft":
        raise ValueError("Only drafts can be published; create a new version")
    root = Path(m["project_root"]).resolve()
    tid = m["scope"]["key"]
    home = thread_home(root, tid)
    if not within(snapshot, home / "snapshots"):
        raise ValueError("Snapshot must be inside this chat's isolated snapshots directory")
    lock = home / "PUBLISH.lock"
    try:
        fd = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        raise ValueError("Publish lock exists; investigate live writer before removing it")
    temp = home / ("CURRENT." + uuid.uuid4().hex + ".tmp")
    try:
        with os.fdopen(fd, "w") as stream:
            stream.write(str(os.getpid()))
        if current(root, tid) != m.get("parent"):
            raise ValueError("CURRENT advanced since collection; merge latest ledger into a new capture")
        if git_state(root) != m.get("git_state"):
            raise ValueError("Project changed during capture; re-check evidence and recollect")
        m["status"], m["sealed_at"] = "sealed", now()
        m["files"] = {}
        for p in sorted(snapshot.rglob("*")):
            if p.is_symlink():
                raise ValueError("Snapshot must not contain symlinks")
            if p.is_file() and p != snapshot / "manifest.json":
                m["files"][p.relative_to(snapshot).as_posix()] = sha(p)
        write_json(snapshot / "manifest.json", m)
        validate(snapshot, sealed=True)
        pointer = {"schema_version": VERSION, "snapshot": snapshot.relative_to(root / ".dtc").as_posix(),
                   "manifest_sha256": sha(snapshot / "manifest.json")}
        with temp.open("x", encoding="utf-8") as stream:
            stream.write(dump(pointer) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, home / "CURRENT.json")
        return {"status": "published", "snapshot": str(snapshot), "scope": m["scope"], "coverage": review["coverage"], "ledger_ids": len(ledger["items"]) + len(ledger["tasks"])}
    finally:
        if temp.exists():
            temp.unlink()
        lock.unlink()


def list_handoffs(root):
    found = []
    for p in sorted((root / ".dtc" / "handoffs").glob("*/CURRENT.json")):
        try:
            folder, _ = locate(root, None, handoff=p.parent.name)
            m = read_json(folder / "manifest.json")
            found.append({"key": p.parent.name, "scope": m.get("scope"), "snapshot": str(folder), "sealed_at": m.get("sealed_at")})
        except (ValueError, OSError, KeyError, TypeError) as exc:
            found.append({"key": p.parent.name, "error": str(exc)})
    return {"handoffs": found, "legacy_project_pointer_exists": (root / ".dtc" / "CURRENT.json").exists()}


def main():
    # PowerShell/redirected Windows pipes may otherwise use a legacy code page.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("inventory", "collect", "check", "publish", "verify", "list"))
    parser.add_argument("--project", default=".")
    parser.add_argument("--snapshot")
    parser.add_argument("--codex-home")
    parser.add_argument("--thread-id", action="append", help="Repeat for explicitly selected source chats; collect defaults to CODEX_THREAD_ID only")
    parser.add_argument("--handoff", help="Existing handoff key to restore")
    parser.add_argument("--label", help="Human-readable label for the explicitly selected chat(s)")
    args = parser.parse_args()
    try:
        if args.command == "list":
            result = list_handoffs(Path(args.project).resolve())
        elif args.command in ("inventory", "collect"):
            result = collect(args)
        elif args.command in ("check", "publish"):
            if not args.snapshot:
                raise ValueError("--snapshot is required")
            folder = Path(args.snapshot).resolve()
            if args.command == "publish":
                result = publish(folder)
            else:
                m, _, review = validate(folder)
                result = {"status": "structure_checked", "snapshot": str(folder), "coverage": review["coverage"], "semantic_completeness_proven": False}
        else:
            root = Path(args.project).resolve()
            folder, pointer = locate(root, args.snapshot, args.thread_id, args.handoff)
            m, ledger, review = validate(folder, sealed=True)
            if args.thread_id and selection_key(args.thread_id) != m["scope"]["key"]:
                raise ValueError("Restored handoff does not match the requested source chats")
            state = git_state(root)
            result = {"status": "verified", "snapshot": str(folder), "coverage": review["coverage"], "gaps": review["gaps"],
                      "scope": m["scope"],
                      "project_root_at_capture": m["project_root"], "project_root_now": str(root),
                      "location_changed": str(root) != m["project_root"], "git_drift": state != m.get("git_state"),
                      "git_state_now": state, "next_task_id": ledger.get("next_task_id"),
                      "read_order": ["START_HERE.md", "ledger.json"] + ledger["read_next"],
                      "is_current_pointer": pointer is not None, "semantic_completeness_proven": False}
        print(dump(result))
        return 0
    except (ValueError, OSError, KeyError, TypeError, AttributeError) as exc:
        print(dump({"status": "error", "detail": str(exc)}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
