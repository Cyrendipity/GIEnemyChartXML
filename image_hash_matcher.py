#!/usr/bin/env python3
"""Match decoded PNG pixels against the runtime icon tree."""

from __future__ import annotations

import hashlib
import struct
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import DefaultDict, Dict, List, Optional, Tuple

from PIL import Image, UnidentifiedImageError


def rgba_pixel_hash(path: Path) -> str:
    """Hash image dimensions and decoded RGBA pixels, ignoring PNG encoding details."""
    with Image.open(path) as image:
        rgba = image.convert("RGBA")
        payload = struct.pack(">II", rgba.width, rgba.height) + rgba.tobytes()
    return hashlib.sha256(payload).hexdigest()


def decoded_image_size(path: Path) -> Tuple[int, int]:
    with Image.open(path) as image:
        return image.size


@dataclass
class IconHashIndex:
    icons_root: Path
    paths_by_hash: Dict[str, Tuple[Path, ...]]
    unreadable_files: List[str] = field(default_factory=list)
    _source_hashes: Dict[Path, Optional[str]] = field(default_factory=dict)

    @classmethod
    def build(cls, icons_root: Path) -> "IconHashIndex":
        if not icons_root.is_dir():
            raise ValueError(f"图标目录不存在：{icons_root.as_posix()}")
        collected: DefaultDict[str, List[Path]] = defaultdict(list)
        unreadable = []
        for path in sorted(icons_root.rglob("*.png")):
            try:
                collected[rgba_pixel_hash(path)].append(path)
            except (OSError, UnidentifiedImageError) as error:
                unreadable.append(f"{path.as_posix()}：{error}")
        return cls(
            icons_root=icons_root.resolve(),
            paths_by_hash={
                digest: tuple(sorted(paths)) for digest, paths in collected.items()
            },
            unreadable_files=unreadable,
        )

    @property
    def file_count(self) -> int:
        return sum(len(paths) for paths in self.paths_by_hash.values())

    @property
    def duplicate_hash_count(self) -> int:
        return sum(len(paths) > 1 for paths in self.paths_by_hash.values())

    def match(self, source_path: Path) -> Optional[str]:
        resolved = source_path.resolve()
        if resolved not in self._source_hashes:
            try:
                self._source_hashes[resolved] = rgba_pixel_hash(resolved)
            except (OSError, UnidentifiedImageError):
                self._source_hashes[resolved] = None
        digest = self._source_hashes[resolved]
        if digest is None:
            return None
        candidates = self.paths_by_hash.get(digest, ())
        if not candidates:
            return None
        relative = candidates[0].resolve().relative_to(self.icons_root)
        if not relative.parts or relative.parts[0] not in {
            "UIIcon",
            "MonsterSkillIcon",
        }:
            return None
        return Path("static/images").joinpath(relative).as_posix()
