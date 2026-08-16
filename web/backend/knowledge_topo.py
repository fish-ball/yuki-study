"""知识点拓扑分层与有序挂树。"""
from __future__ import annotations

import json
from typing import Any


LEVELS = ("L0", "L1", "L2", "L3", "L4")


def empty_stats() -> dict[str, int | float]:
    return {
        "L0": 0,
        "L1": 0,
        "L2": 0,
        "L3": 0,
        "L4": 0,
        "total": 0,
        "proficient": 0,
        "proficient_pct": 0.0,
    }


def bump_stats(stats: dict[str, int | float], level: str | None) -> None:
    lv = level if level in LEVELS else "L0"
    stats[lv] = int(stats[lv]) + 1
    stats["total"] = int(stats["total"]) + 1
    if lv in ("L3", "L4"):
        stats["proficient"] = int(stats["proficient"]) + 1
    total = int(stats["total"])
    stats["proficient_pct"] = round(100.0 * int(stats["proficient"]) / total, 1) if total else 0.0


def parse_json_list(raw: str | None) -> list[str]:
    try:
        data = json.loads(raw or "[]")
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    return [str(x) for x in data]


def compute_topo_depths(nodes: list[dict[str, Any]]) -> dict[str, int]:
    """按 prerequisites 最长路径分层；环或缺前置时回退 sort_index 量级。"""
    by_id = {n["id"]: n for n in nodes}
    depths: dict[str, int] = {}
    visiting: set[str] = set()

    def depth_of(nid: str) -> int:
        if nid in depths:
            return depths[nid]
        if nid in visiting:
            # 环：用 sort_index 量级，避免无限递归
            node = by_id.get(nid) or {}
            return int(node.get("sort_index") or 0) % 1000
        if nid not in by_id:
            return 0
        visiting.add(nid)
        node = by_id[nid]
        prereqs = node.get("prerequisites")
        if prereqs is None:
            prereqs = parse_json_list(node.get("prerequisites_json"))
        best = 0
        for pid in prereqs or []:
            if pid in by_id:
                best = max(best, depth_of(pid) + 1)
            # 缺失前置忽略，不抬高深度
        visiting.discard(nid)
        depths[nid] = best
        return best

    for n in nodes:
        depth_of(n["id"])
    return depths


def sort_siblings(
    children: list[dict[str, Any]],
    parent_children_ids: list[str] | None,
) -> list[dict[str, Any]]:
    """兄弟顺序：父节点 children 列表优先，其余按 sort_index。"""
    order_map = {cid: i for i, cid in enumerate(parent_children_ids or [])}

    def key(n: dict[str, Any]) -> tuple[int, int, str]:
        if n["id"] in order_map:
            return (0, order_map[n["id"]], n["id"])
        return (1, int(n.get("sort_index") or 0), n["id"])

    return sorted(children, key=key)


def build_ordered_tree(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """按 parent 挂树，子节点按 children_json / sort_index 排序。"""
    enriched = []
    for n in nodes:
        item = dict(n)
        if "prerequisites" not in item:
            item["prerequisites"] = parse_json_list(item.pop("prerequisites_json", None))
        elif "prerequisites_json" in item:
            item.pop("prerequisites_json", None)
        if "children_ids" not in item:
            item["children_ids"] = parse_json_list(item.pop("children_json", None))
        elif "children_json" in item:
            item.pop("children_json", None)
        item["children"] = []
        enriched.append(item)

    depths = compute_topo_depths(enriched)
    by_id = {}
    for item in enriched:
        item["topo_depth"] = depths.get(item["id"], 0)
        by_id[item["id"]] = item

    roots: list[dict[str, Any]] = []
    for item in by_id.values():
        parent = item.get("parent_id")
        if parent and parent in by_id:
            by_id[parent]["children"].append(item)
        else:
            roots.append(item)

    def sort_recursive(node: dict[str, Any]) -> None:
        node["children"] = sort_siblings(node["children"], node.get("children_ids"))
        for child in node["children"]:
            sort_recursive(child)

    roots.sort(key=lambda n: (int(n.get("sort_index") or 0), n["id"]))
    for r in roots:
        sort_recursive(r)

    def strip_internal(node: dict[str, Any]) -> None:
        node.pop("children_ids", None)
        for child in node.get("children") or []:
            strip_internal(child)

    for r in roots:
        strip_internal(r)
    return roots


def build_lattice(nodes: list[dict[str, Any]]) -> dict[str, Any]:
    """
    紧凑点阵：根模块为列，叶节点按 topo_depth / sort_index 排布。
    """
    enriched = []
    for n in nodes:
        item = dict(n)
        if "prerequisites" not in item:
            item["prerequisites"] = parse_json_list(item.get("prerequisites_json"))
        item.pop("prerequisites_json", None)
        item["children_ids"] = parse_json_list(item.get("children_json"))
        item.pop("children_json", None)
        enriched.append(item)

    depths = compute_topo_depths(enriched)
    by_id = {n["id"]: n for n in enriched}
    for n in enriched:
        n["topo_depth"] = depths.get(n["id"], 0)

    # 有 parent 且不在任何人的 children 里也可能是叶；叶 = children_ids 为空
    leaves = [n for n in enriched if not n.get("children_ids")]
    modules = [
        n
        for n in enriched
        if not n.get("parent_id")
    ]
    modules.sort(key=lambda n: (int(n.get("sort_index") or 0), n["id"]))

    def module_root_of(node: dict[str, Any]) -> str | None:
        cur = node
        guard = 0
        while cur.get("parent_id") and guard < 30:
            guard += 1
            parent = by_id.get(cur["parent_id"])
            if not parent:
                break
            cur = parent
        return cur["id"] if not cur.get("parent_id") else cur.get("parent_id")

    stats = empty_stats()
    module_payload = []
    for mod in modules:
        mstats = empty_stats()
        mod_leaves = [
            n
            for n in leaves
            if module_root_of(n) == mod["id"]
        ]
        mod_leaves.sort(
            key=lambda n: (
                int(n.get("topo_depth") or 0),
                int(n.get("sort_index") or 0),
                n["id"],
            )
        )
        out_nodes = []
        for n in mod_leaves:
            level = n.get("level") or "L0"
            bump_stats(mstats, level)
            bump_stats(stats, level)
            out_nodes.append(
                {
                    "id": n["id"],
                    "name": n["name"],
                    "level": level,
                    "topo_depth": n.get("topo_depth") or 0,
                    "exam_weight": n.get("exam_weight") or "mid",
                    "has_tutorial": bool(n.get("has_tutorial")),
                    "wrong_count": n.get("wrong_count") or 0,
                    "prerequisites": n.get("prerequisites") or [],
                }
            )
        module_payload.append(
            {
                "id": mod["id"],
                "name": mod["name"],
                "stats": mstats,
                "nodes": out_nodes,
            }
        )

    return {"stats": stats, "modules": module_payload}
