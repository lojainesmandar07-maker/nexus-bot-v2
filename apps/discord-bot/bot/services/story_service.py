from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional


logger = logging.getLogger("nexus.discord.story_service")


class StoryContentError(RuntimeError):
    """Raised when story content is invalid or missing."""


@dataclass(frozen=True)
class WorldBundle:
    world_id: str
    metadata: dict[str, Any]
    parts: dict[str, dict[str, Any]]
    endings: dict[str, dict[str, Any]]
    start_part_id: str


@dataclass
class StoryService:
    """
    Production file-backed story service.

    Supported content formats:
    1) Legacy single-file world JSON:
       {
         "metadata": {...},
         "parts": {...},
         "endings": {...}
       }

    2) Split world folder:
       content/worlds/<world_id>/
         - world.json (metadata + optional start_part_id)
         - parts/*.json
         - endings.json (optional)

    Guarantees after load_runtime_bundles():
      - no broken next pointers
      - no dead-end non-ending nodes
      - no unreachable parts from start
      - optional loop protection (can be configured)
    """

    content_root: Path
    strict_validation: bool = True
    allow_loops: bool = False
    max_parts_per_world: int = 20000

    _bundles: dict[str, WorldBundle] = field(default_factory=dict, init=False)

    # ---------------------------------------------------------
    # Public API used by bot/app.py and commands/story.py
    # ---------------------------------------------------------

    async def healthcheck(self) -> None:
        if not self.content_root.exists():
            raise StoryContentError(f"Content root does not exist: {self.content_root}")
        if not self.content_root.is_dir():
            raise StoryContentError(f"Content root must be directory: {self.content_root}")

        worlds_dir = self.content_root / "worlds"
        if not worlds_dir.exists():
            raise StoryContentError(f"Missing worlds directory: {worlds_dir}")

    async def load_runtime_bundles(self) -> None:
        await self.healthcheck()

        worlds_dir = self.content_root / "worlds"
        world_ids = sorted([p.name for p in worlds_dir.iterdir() if p.is_dir()])

        if not world_ids:
            raise StoryContentError(f"No world folders found in: {worlds_dir}")

        bundles: dict[str, WorldBundle] = {}

        for world_id in world_ids:
            bundle = await self._load_world_bundle(world_id)
            self._validate_bundle(bundle)
            bundles[world_id] = bundle

        self._bundles = bundles
        logger.info("Loaded %d world bundles", len(self._bundles))

    async def get_start_part_id(self, world_id: str) -> str:
        bundle = self._require_bundle(world_id)
        return bundle.start_part_id

    async def get_part(self, world_id: str, part_id: str) -> Optional[dict]:
        bundle = self._require_bundle(world_id)
        return bundle.parts.get(part_id)

    async def is_ending(self, world_id: str, part_id: str) -> bool:
        bundle = self._require_bundle(world_id)
        if part_id in bundle.endings:
            return True

        part = bundle.parts.get(part_id)
        if not part:
            return False

        choices = part.get("choices", [])
        return len(choices) == 0

    async def get_ending(self, world_id: str, ending_id: str) -> Optional[dict]:
        bundle = self._require_bundle(world_id)
        return bundle.endings.get(ending_id)

    async def list_worlds(self) -> list[str]:
        return sorted(self._bundles.keys())

    # ---------------------------------------------------------
    # Internal loading
    # ---------------------------------------------------------

    async def _load_world_bundle(self, world_id: str) -> WorldBundle:
        world_dir = self.content_root / "worlds" / world_id
        if not world_dir.exists():
            raise StoryContentError(f"World folder missing: {world_dir}")

        legacy_file = world_dir / f"{world_id}_story.json"
        if legacy_file.exists():
            return await self._load_legacy_single_file(world_id, legacy_file)

        world_json = world_dir / "world.json"
        parts_dir = world_dir / "parts"
        endings_json = world_dir / "endings.json"

        if not world_json.exists():
            raise StoryContentError(f"Missing world.json for world '{world_id}'")

        if not parts_dir.exists() or not parts_dir.is_dir():
            raise StoryContentError(f"Missing parts directory for world '{world_id}'")

        metadata = await self._read_json(world_json)
        parts = await self._read_parts_dir(parts_dir)
        endings = await self._read_json(endings_json) if endings_json.exists() else {}

        if not isinstance(endings, dict):
            raise StoryContentError(f"endings.json must be object for world '{world_id}'")

        # normalize endings if wrapped as {"endings": {...}}
        if "endings" in endings and isinstance(endings["endings"], dict):
            endings = endings["endings"]

        start_part_id = self._resolve_start_part_id(world_id, metadata, parts)

        return WorldBundle(
            world_id=world_id,
            metadata=metadata,
            parts=parts,
            endings=endings,
            start_part_id=start_part_id,
        )

    async def _load_legacy_single_file(self, world_id: str, file_path: Path) -> WorldBundle:
        data = await self._read_json(file_path)
        if not isinstance(data, dict):
            raise StoryContentError(f"Legacy story file must be object: {file_path}")

        metadata = data.get("metadata", {})
        parts = data.get("parts", {})
        endings = data.get("endings", {})

        if not isinstance(parts, dict):
            raise StoryContentError(f"'parts' must be object in legacy file: {file_path}")
        if not isinstance(endings, dict):
            raise StoryContentError(f"'endings' must be object in legacy file: {file_path}")

        start_part_id = self._resolve_start_part_id(world_id, metadata, parts)

        return WorldBundle(
            world_id=world_id,
            metadata=metadata,
            parts=parts,
            endings=endings,
            start_part_id=start_part_id,
        )

    async def _read_parts_dir(self, parts_dir: Path) -> dict[str, dict[str, Any]]:
        files = sorted(parts_dir.glob("*.json"))
        if not files:
            raise StoryContentError(f"No part files found in: {parts_dir}")

        parts: dict[str, dict[str, Any]] = {}
        for file in files:
            node = await self._read_json(file)
            if not isinstance(node, dict):
                raise StoryContentError(f"Part file must be object: {file}")

            part_id = str(node.get("id") or file.stem).strip()
            if not part_id:
                raise StoryContentError(f"Missing part id in file: {file}")

            if part_id in parts:
                raise StoryContentError(f"Duplicate part id '{part_id}' in {parts_dir}")

            # ensure part id is present inside node
            node["id"] = part_id
            parts[part_id] = node

        return parts

    async def _read_json(self, path: Path) -> Any:
        try:
            raw = await asyncio.to_thread(path.read_text, encoding="utf-8")
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise StoryContentError(f"Invalid JSON in {path}: {exc}") from exc
        except Exception as exc:
            raise StoryContentError(f"Failed reading {path}: {exc}") from exc

    # ---------------------------------------------------------
    # Validation
    # ---------------------------------------------------------

    def _validate_bundle(self, bundle: WorldBundle) -> None:
        world_id = bundle.world_id
        parts = bundle.parts
        endings = bundle.endings

        if not parts:
            raise StoryContentError(f"[{world_id}] world has no parts")

        if len(parts) > self.max_parts_per_world:
            raise StoryContentError(f"[{world_id}] too many parts ({len(parts)})")

        if bundle.start_part_id not in parts:
            raise StoryContentError(f"[{world_id}] start_part_id '{bundle.start_part_id}' not found in parts")

        # node-level validation
        for part_id, part in parts.items():
            self._validate_part_shape(world_id, part_id, part)

        # graph-level validation
        self._validate_graph_links(world_id, parts, endings)
        self._validate_no_dead_end_non_endings(world_id, parts, endings)
        self._validate_reachability(world_id, bundle.start_part_id, parts, endings)

        if not self.allow_loops:
            self._validate_no_loops(world_id, bundle.start_part_id, parts, endings)

    def _validate_part_shape(self, world_id: str, part_id: str, part: dict[str, Any]) -> None:
        if "title" not in part:
            raise StoryContentError(f"[{world_id}] part '{part_id}' missing title")
        if "text" not in part:
            raise StoryContentError(f"[{world_id}] part '{part_id}' missing text")
        if "choices" not in part:
            raise StoryContentError(f"[{world_id}] part '{part_id}' missing choices")

        choices = part.get("choices")
        if not isinstance(choices, list):
            raise StoryContentError(f"[{world_id}] part '{part_id}' choices must be array")

        # You may allow 0 choices only for ending-like nodes
        if len(choices) == 0:
            # allowed only if this node is explicitly flagged as ending node
            if part.get("ending") is None and part.get("ending_type") is None:
                # still allowed if it is handled via global endings dict with same id
                # final check done later in dead-end validator
                pass

        for idx, ch in enumerate(choices):
            if not isinstance(ch, dict):
                raise StoryContentError(f"[{world_id}] part '{part_id}' choice[{idx}] must be object")
            if "text" not in ch:
                raise StoryContentError(f"[{world_id}] part '{part_id}' choice[{idx}] missing text")
            if "next_part_id" not in ch and "next" not in ch:
                raise StoryContentError(f"[{world_id}] part '{part_id}' choice[{idx}] missing next_part_id/next")

    def _validate_graph_links(
        self,
        world_id: str,
        parts: dict[str, dict[str, Any]],
        endings: dict[str, dict[str, Any]],
    ) -> None:
        part_ids = set(parts.keys())
        ending_ids = set(endings.keys())

        for part_id, part in parts.items():
            for idx, ch in enumerate(part.get("choices", [])):
                nxt = self._choice_next(ch)
                if not nxt:
                    raise StoryContentError(f"[{world_id}] part '{part_id}' choice[{idx}] empty next pointer")
                if nxt not in part_ids and nxt not in ending_ids:
                    raise StoryContentError(
                        f"[{world_id}] broken link: part '{part_id}' choice[{idx}] -> '{nxt}' not found"
                    )

                fail_nxt = ch.get("fail_next")
                if fail_nxt and fail_nxt not in part_ids and fail_nxt not in ending_ids:
                    raise StoryContentError(
                        f"[{world_id}] broken fail_next: part '{part_id}' choice[{idx}] -> '{fail_nxt}' not found"
                    )

    def _validate_no_dead_end_non_endings(
        self,
        world_id: str,
        parts: dict[str, dict[str, Any]],
        endings: dict[str, dict[str, Any]],
    ) -> None:
        ending_ids = set(endings.keys())

        for part_id, part in parts.items():
            choices = part.get("choices", [])
            if len(choices) > 0:
                continue

            is_explicit_ending_node = (
                part_id in ending_ids
                or part.get("ending") is not None
                or part.get("ending_type") is not None
            )
            if not is_explicit_ending_node:
                raise StoryContentError(
                    f"[{world_id}] dead-end node '{part_id}' has no choices and is not marked ending"
                )

    def _validate_reachability(
        self,
        world_id: str,
        start_part_id: str,
        parts: dict[str, dict[str, Any]],
        endings: dict[str, dict[str, Any]],
    ) -> None:
        visited = set()
        stack = [start_part_id]
        part_ids = set(parts.keys())
        ending_ids = set(endings.keys())

        while stack:
            node = stack.pop()
            if node in visited:
                continue
            visited.add(node)

            # only parts can fan out
            if node not in part_ids:
                continue

            for ch in parts[node].get("choices", []):
                nxt = self._choice_next(ch)
                if nxt and nxt in part_ids.union(ending_ids):
                    stack.append(nxt)

                fail_nxt = ch.get("fail_next")
                if fail_nxt and fail_nxt in part_ids.union(ending_ids):
                    stack.append(fail_nxt)

        unreachable_parts = sorted(set(parts.keys()) - visited)
        if unreachable_parts and self.strict_validation:
            preview = ", ".join(unreachable_parts[:10])
            raise StoryContentError(f"[{world_id}] unreachable parts detected (sample): {preview}")

        unreachable_endings = sorted(set(endings.keys()) - visited)
        if unreachable_endings and self.strict_validation:
            preview = ", ".join(unreachable_endings[:10])
            raise StoryContentError(f"[{world_id}] unreachable endings detected (sample): {preview}")

    def _validate_no_loops(
        self,
        world_id: str,
        start_part_id: str,
        parts: dict[str, dict[str, Any]],
        endings: dict[str, dict[str, Any]],
    ) -> None:
        """
        DFS cycle detection on part graph (endings are treated as terminal).
        """
        adjacency: dict[str, list[str]] = {}
        ending_ids = set(endings.keys())
        for part_id, part in parts.items():
            targets: list[str] = []
            for ch in part.get("choices", []):
                nxt = self._choice_next(ch)
                if nxt and nxt in parts and nxt not in ending_ids:
                    targets.append(nxt)
                fail_nxt = ch.get("fail_next")
                if fail_nxt and fail_nxt in parts and fail_nxt not in ending_ids:
                    targets.append(fail_nxt)
            adjacency[part_id] = targets

        WHITE, GRAY, BLACK = 0, 1, 2
        color = {k: WHITE for k in adjacency.keys()}

        def dfs(node: str) -> bool:
            color[node] = GRAY
            for nxt in adjacency.get(node, []):
                if color[nxt] == GRAY:
                    return True
                if color[nxt] == WHITE and dfs(nxt):
                    return True
            color[node] = BLACK
            return False

        # detect from all nodes (not only start) to catch hidden cycles
        for node in adjacency.keys():
            if color[node] == WHITE and dfs(node):
                raise StoryContentError(f"[{world_id}] cycle detected in story graph (loops not allowed)")

    # ---------------------------------------------------------
    # Helpers
    # ---------------------------------------------------------

    def _choice_next(self, choice: dict[str, Any]) -> Optional[str]:
        nxt = choice.get("next_part_id", choice.get("next"))
        if nxt is None:
            return None
        return str(nxt)

    def _resolve_start_part_id(
        self,
        world_id: str,
        metadata: dict[str, Any],
        parts: dict[str, dict[str, Any]],
    ) -> str:
        # priority:
        # 1) metadata.start_part_id
        # 2) metadata.start_part
        # 3) lexicographically smallest part id
        start = metadata.get("start_part_id") or metadata.get("start_part")
        if start and str(start) in parts:
            return str(start)

        if not parts:
            raise StoryContentError(f"[{world_id}] cannot resolve start part; no parts found")

        return sorted(parts.keys())[0]

    def _require_bundle(self, world_id: str) -> WorldBundle:
        if not self._bundles:
            raise StoryContentError("Story bundles are not loaded. Call load_runtime_bundles() first.")

        bundle = self._bundles.get(world_id)
        if not bundle:
            available = ", ".join(sorted(self._bundles.keys()))
            raise StoryContentError(f"Unknown world '{world_id}'. Available: {available}")
        return bundle
