"""Verify ai-inventory.yaml against this tree (Lucy / agent-bom ingest).

Crucible's agent-bom worker is a separate scanner. This module is the in-repo
ingest: load the named harness, confirm every prompt and guardrail still exists
in source, and emit the `ai_inventory.ast_analysis` shape Crucible reconciles.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
INVENTORY_PATH = ROOT / "ai-inventory.yaml"


class InventoryError(ValueError):
    """The named inventory does not match the live tree."""


def _parse_block_list(section: str) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    current: dict[str, str] = {}
    for raw in section.splitlines():
        if raw.startswith("  - "):
            if current:
                items.append(current)
            current = {}
            key, _, value = raw[4:].partition(":")
            current[key.strip()] = value.strip()
        elif raw.startswith("    ") and current:
            key, _, value = raw.strip().partition(":")
            current[key.strip()] = value.strip()
    if current:
        items.append(current)
    return items


def load_inventory_yaml(path: Path | None = None) -> dict[str, Any]:
    text = (path or INVENTORY_PATH).read_text(encoding="utf-8")
    harness: dict[str, str] = {}
    in_harness = False
    sections: dict[str, str] = {}
    current_section = ""
    chunks: dict[str, list[str]] = {"prompts": [], "guardrails": [], "tools": []}

    for line in text.splitlines():
        if line.startswith("harness:"):
            in_harness = True
            current_section = ""
            continue
        if line.startswith("prompts:"):
            in_harness = False
            current_section = "prompts"
            continue
        if line.startswith("guardrails:"):
            in_harness = False
            current_section = "guardrails"
            continue
        if line.startswith("tools:"):
            in_harness = False
            current_section = "tools"
            continue
        if in_harness and line.startswith("  ") and ":" in line:
            key, _, value = line.strip().partition(":")
            harness[key.strip()] = value.strip()
            continue
        if current_section:
            chunks.setdefault(current_section, []).append(line)

    for name, lines in chunks.items():
        sections[name] = "\n".join(lines)

    tools_raw = sections.get("tools", "").strip()
    tools: list[dict[str, str]] = []
    if tools_raw and tools_raw != "[]":
        tools = _parse_block_list(tools_raw)

    return {
        "harness": harness,
        "prompts": _parse_block_list(sections.get("prompts", "")),
        "guardrails": _parse_block_list(sections.get("guardrails", "")),
        "tools": tools,
    }


def _file_defines(path: Path, name: str) -> bool:
    source = path.read_text(encoding="utf-8")
    folded = name.casefold()
    if folded not in source.casefold():
        return False
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return True
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if node.name.casefold() == folded:
                return True
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id.casefold() == folded:
                    return True
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if node.target.id.casefold() == folded:
                return True
        if isinstance(node, ast.Attribute) and node.attr.casefold() == folded:
            return True
    return folded in source.casefold()


def ingest_ai_inventory(path: Path | None = None) -> dict[str, Any]:
    """Load YAML, verify symbols against the tree, return Crucible ast_analysis."""
    inventory = load_inventory_yaml(path)
    missing: list[str] = []

    def _check(items: list[dict[str, str]], kind: str) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for item in items:
            name = item.get("name") or ""
            rel = item.get("file") or ""
            file_path = ROOT / rel
            if not rel or not file_path.is_file():
                missing.append(f"{kind} {name}: missing file {rel}")
                continue
            if name and not _file_defines(file_path, name):
                missing.append(f"{kind} {name}: not found in {rel}")
                continue
            rows.append({
                "name": name,
                "file": rel,
                "source_id": item.get("id") or "",
                "description": item.get("description") or "",
                "used_by": item.get("used_by") or "",
            })
        return rows

    prompts = _check(inventory["prompts"], "prompt")
    guardrails = _check(inventory["guardrails"], "guardrail")
    tools = _check(inventory["tools"], "tool")
    if missing:
        raise InventoryError("; ".join(missing))

    return {
        "ai_inventory": {
            "harness": inventory["harness"],
            "ast_analysis": {
                "prompts": prompts,
                "guardrails": guardrails,
                "tools": tools,
            },
        }
    }


def write_scan_report(dest: Path | None = None) -> Path:
    report = ingest_ai_inventory()
    out = dest or (ROOT / "ai-inventory.scan.json")
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return out


if __name__ == "__main__":
    path = write_scan_report()
    print(path)
