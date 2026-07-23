#!/usr/bin/env python3
"""从 GIEnemyChart 的 FXG/MXML 生成并汇总怪物 JSON。"""

from __future__ import annotations

import argparse
import copy
import difflib
import html
import json
import re
import shutil
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

from image_hash_matcher import IconHashIndex, decoded_image_size


ELEMENT_ORDER = (
    "Pyro",
    "Hydro",
    "Anemo",
    "Electro",
    "Dendro",
    "Cryo",
    "Geo",
    "Physical",
)

ELEMENT_COLORS = {
    "Pyro": "#ff9999",
    "Hydro": "#7fbfff",
    "Anemo": "#7fffd7",
    "Electro": "#ffacff",
    "Dendro": "#99ff85",
    "Cryo": "#99ffff",
    "Geo": "#ffe599",
    "Physical": "#ebe5d8",
}

SHIELD_ELEMENTS = {
    "火": "Fire",
    "水": "Water",
    "雷": "Electric",
    "岩": "Rock",
    "冰": "Ice",
    "冻": "Frozen",
    "草": "Grass",
    "木": "Wood",
}

SKILL_SUFFIX_REJECT = (
    "Data",
    "Lv.",
    "生命值",
    "伤害",
    "恢复",
    "后",
)

MECHANIC_KEYWORDS = (
    "状态",
    "抗性",
    "瘫痪",
    "盾",
    "护罩",
    "屏障",
    "护甲",
    "胄甲",
    "充能",
    "计量",
    "热量",
    "怒气",
    "进度",
    "生命值",
    "击破",
    "破坏",
    "持续",
)

CURVE_CODES = {
    "H": ("HP_1", "GROW_CURVE_HP"),
    "F": ("HP_2", "GROW_CURVE_HP_2"),
    "P": ("HP_3", "GROW_CURVE_HP_ENVIRONMENT"),
    "B0": ("ATK_1", "GROW_CURVE_ATTACK"),
    "B1": ("ATK_1", "GROW_CURVE_ATTACK"),
    "B2": ("ATK_2", "GROW_CURVE_ATTACK_2"),
}

ENERGY_TYPES = {
    "P": "Pyro",
    "H": "Hydro",
    "E": "Electro",
    "C": "Cryo",
    "D": "Dendro",
    "A": "Anemo",
    "G": "Geo",
    "W": "White",
}

CATEGORY_ALIASES = {
    "EnemiesOfNote": "EnemyOfNote",
    "Hilichurls": "Hilichurls",
    "LeyLine": "StygianOnslaught",
    "MysticalBeasts": "MagicalBeasts",
}

TIER_KEYWORDS = (
    ("地方传奇", "Legend"),
    ("剧情", "Quest"),
    ("试炼首领", "Boss"),
    ("首领", "Boss"),
    ("精英", "Elite"),
    ("普通", "Common"),
    ("动物", "Common"),
)

LEYLINE_DEFAULT_LEVELS = {
    "N1": 40,
    "N2": 70,
    "N3": 90,
    "N4": 100,
    "N5": 105,
    "N6": 110,
}

LEYLINE_TIP_NAMES = {
    "PYRO": "Pyro",
    "HYDRO": "Hydro",
    "ANEMO": "Anemo",
    "ELECTRO": "Electro",
    "DENDRO": "Dendro",
    "CRYO": "Cryo",
    "GEO": "Geo",
    "MELT": "Melt",
    "EVAPORATE": "Evaporate",
    "AGGREGATE": "Quicken",
    "OVERLOADED": "Overloaded",
    "BLOOM": "Bloom",
    "E_CHARGED": "ElectroCharged",
    "E_SWIRL": "ElectroSwirl",
    "P_SWIRL": "PyroSwirl",
    "LUNAR_REAC": "LunarReaction",
    "MOONSIGN_2": "Moonsign",
    "MULTI_ELEM": "MultiElement",
    "SINGLE_ELEM": "SingleElement",
    "NIGHTSOUL": "Nightsoul",
    "HIGH_FREQ": "HighFrequency",
    "LOW_FREQ": "LowFrequency",
    "PLUNGING": "Plunging",
    "RANGED": "Ranged",
    "MELEE": "Melee",
    "SHIELD": "Shield",
    "HEALING": "Healing",
    "HPDEBT": "BondOfLife",
}


@dataclass
class TextBlock:
    plain: str
    rich: str
    x: float
    y: float
    width: float
    height: float
    font_size: Optional[float]
    color: Optional[str]
    visible: bool = True


@dataclass
class ImageBlock:
    source: str
    x: float
    y: float
    width: float
    height: float


@dataclass
class SourceDocument:
    path: Path
    width: float
    height: float
    texts: List[TextBlock] = field(default_factory=list)
    images: List[ImageBlock] = field(default_factory=list)


@dataclass
class MonsterSource:
    category: str
    name: str
    fxg: Optional[Path] = None
    mxml: Optional[Path] = None


@dataclass
class ReferenceCatalog:
    identifiers: Dict[str, Tuple[str, str]] = field(default_factory=dict)
    skills: Dict[Tuple[str, str], str] = field(default_factory=dict)
    shields: Dict[Tuple[str, str], str] = field(default_factory=dict)
    chargebars: Dict[Tuple[str, str], str] = field(default_factory=dict)


def parse_xml_root(path: Path) -> ET.Element:
    try:
        return ET.parse(path).getroot()
    except ET.ParseError:
        source = path.read_text(encoding="utf-8-sig")
        source = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", source)
        source = re.sub(
            r"&(?!#\d+;|#x[0-9a-fA-F]+;|amp;|lt;|gt;|quot;|apos;)",
            "&amp;",
            source,
        )
        return ET.fromstring(source)


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def number(value: Optional[str], default: float = 0.0) -> float:
    if value is None:
        return default
    try:
        return float(value.strip())
    except (TypeError, ValueError):
        return default


def normalize_text(value: str) -> str:
    value = value.replace("\r\n", "\n").replace("\r", "\n").replace("\u00a0", " ")
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in value.split("\n")]
    while lines and not lines[0]:
        lines.pop(0)
    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines)


def normalize_rich(value: str) -> str:
    value = value.replace("\r\n", "\n").replace("\r", "\n").replace("\u00a0", " ")
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in value.split("\n")]
    while lines and not lines[0]:
        lines.pop(0)
    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines)


def plain_xml(node: ET.Element) -> str:
    if local_name(node.tag) == "br":
        return "\n"
    parts = [node.text or ""]
    for child in node:
        parts.append(plain_xml(child))
        parts.append(child.tail or "")
    return "".join(parts)


def rich_xml(node: ET.Element) -> str:
    if local_name(node.tag) == "br":
        return "\n"
    parts = [node.text or ""]
    for child in node:
        parts.append(rich_xml(child))
        parts.append(child.tail or "")
    content = "".join(parts)
    if local_name(node.tag) != "span":
        return content
    if node.attrib.get("textDecoration", "").lower() == "underline":
        content = f"<u>{content}</u>"
    color = node.attrib.get("color", "").lower()
    if color and color not in {"#ffffff", "#fff"}:
        content = f"<color={color}>{content}</color>"
    return content


def content_nodes(block: ET.Element) -> List[ET.Element]:
    content = next((child for child in block if local_name(child.tag) == "content"), None)
    if content is None:
        return [block]
    paragraphs = [child for child in content if local_name(child.tag) in {"p", "div"}]
    return paragraphs or [content]


def block_font_size(block: ET.Element) -> Optional[float]:
    if "fontSize" in block.attrib:
        return number(block.attrib["fontSize"])
    for node in block.iter():
        if "fontSize" in node.attrib:
            return number(node.attrib["fontSize"])
    return None


def block_color(block: ET.Element) -> Optional[str]:
    if block.attrib.get("color"):
        return block.attrib["color"].lower()
    for node in block.iter():
        if node.attrib.get("color"):
            return node.attrib["color"].lower()
    return None


def parse_fxg(path: Path) -> SourceDocument:
    root = parse_xml_root(path)
    document = SourceDocument(
        path=path,
        width=number(root.attrib.get("viewWidth")),
        height=number(root.attrib.get("viewHeight")),
    )

    def visit(node: ET.Element, offset_x: float, offset_y: float) -> None:
        tag = local_name(node.tag)
        own_x = number(node.attrib.get("x"))
        own_y = number(node.attrib.get("y"))
        absolute_x = offset_x + own_x
        absolute_y = offset_y + own_y
        if tag == "RichText":
            nodes = content_nodes(node)
            plain = normalize_text("\n".join(plain_xml(item) for item in nodes))
            rich = normalize_rich("\n".join(rich_xml(item) for item in nodes))
            if plain:
                document.texts.append(
                    TextBlock(
                        plain=plain,
                        rich=rich,
                        x=absolute_x,
                        y=absolute_y,
                        width=number(node.attrib.get("width")),
                        height=number(node.attrib.get("height")),
                        font_size=block_font_size(node),
                        color=block_color(node),
                        visible=node.attrib.get("visible", "true").lower() != "false",
                    )
                )
        elif tag == "BitmapImage":
            document.images.append(
                ImageBlock(
                    source=node.attrib.get("source", ""),
                    x=absolute_x,
                    y=absolute_y,
                    width=number(node.attrib.get("width")),
                    height=number(node.attrib.get("height")),
                )
            )
        child_x = absolute_x if tag in {"Group", "Graphic"} else offset_x
        child_y = absolute_y if tag in {"Group", "Graphic"} else offset_y
        for child in node:
            visit(child, child_x, child_y)

    visit(root, 0.0, 0.0)
    return document


def parse_mxml_styles(root: ET.Element) -> Dict[str, float]:
    styles: Dict[str, float] = {}
    for node in root.iter():
        if local_name(node.tag) != "Style":
            continue
        css = "".join(node.itertext())
        for match in re.finditer(r"\.([\w-]+)\s*\{(.*?)\}", css, re.S):
            size = re.search(r"fontSize\s*:\s*([0-9.]+)", match.group(2))
            if size:
                styles[match.group(1)] = float(size.group(1))
    return styles


def parse_mxml(path: Path) -> SourceDocument:
    root = parse_xml_root(path)
    styles = parse_mxml_styles(root)
    document = SourceDocument(
        path=path,
        width=number(root.attrib.get("width")),
        height=number(root.attrib.get("height")),
    )
    for node in root.iter():
        tag = local_name(node.tag)
        if tag == "Text":
            plain = normalize_text(node.attrib.get("text", ""))
            if not plain:
                continue
            style_name = node.attrib.get("styleName", "")
            font_size = number(node.attrib.get("fontSize")) if node.attrib.get("fontSize") else styles.get(style_name)
            document.texts.append(
                TextBlock(
                    plain=plain,
                    rich=plain,
                    x=number(node.attrib.get("x")),
                    y=number(node.attrib.get("y")),
                    width=number(node.attrib.get("width")),
                    height=number(node.attrib.get("height")),
                    font_size=font_size,
                    color=node.attrib.get("color", "").lower() or None,
                    visible=node.attrib.get("visible", "true").lower() != "false",
                )
            )
        elif tag == "Image":
            document.images.append(
                ImageBlock(
                    source=node.attrib.get("source", ""),
                    x=number(node.attrib.get("x")),
                    y=number(node.attrib.get("y")),
                    width=number(node.attrib.get("width")),
                    height=number(node.attrib.get("height")),
                )
            )
    return document


def rgb(color: str) -> Optional[Tuple[int, int, int]]:
    if not re.fullmatch(r"#[0-9a-fA-F]{6}", color):
        return None
    return tuple(int(color[index : index + 2], 16) for index in (1, 3, 5))


def color_element(color: Optional[str]) -> Optional[str]:
    if not color:
        return None
    value = rgb(color)
    if value is None:
        return None
    best_name = None
    best_distance = 10**9
    for name, canonical in ELEMENT_COLORS.items():
        expected = rgb(canonical)
        if expected is None:
            continue
        distance = sum((left - right) ** 2 for left, right in zip(value, expected))
        if distance < best_distance:
            best_name = name
            best_distance = distance
    return best_name if best_distance <= 100 else None


def resistance_value(value: str):
    value = value.strip().rstrip("%")
    if value.upper() == "IMM":
        return "IMM"
    if re.fullmatch(r"-?\d+(?:\.\d+)?[kK]", value):
        parsed = float(value[:-1]) * 1000
        return int(parsed) if parsed.is_integer() else parsed
    parsed = float(value)
    return int(parsed) if parsed.is_integer() else parsed


def cluster_rows(blocks: Sequence[Tuple[TextBlock, str]]) -> List[List[Tuple[TextBlock, str]]]:
    rows: List[List[Tuple[TextBlock, str]]] = []
    for item in sorted(blocks, key=lambda pair: (pair[0].y, pair[0].x)):
        row = next((existing for existing in rows if abs(existing[0][0].y - item[0].y) <= 8), None)
        if row is None:
            rows.append([item])
        else:
            row.append(item)
    return rows


def extract_resistance(document: Optional[SourceDocument], warnings: List[str]) -> Dict[str, dict]:
    if document is None:
        warnings.append("缺少 FXG，无法从 MXML 图片中恢复抗性数字")
        return {}
    labels = [block for block in document.texts if "抗性数据" in block.plain and block.visible]
    if not labels:
        warnings.append("未找到抗性数据标题")
        return {}
    label = labels[0]
    candidates: List[Tuple[TextBlock, str]] = []
    for block in document.texts:
        if not block.visible or not re.fullmatch(r"(?:-?\d+(?:\.\d+)?%?|\d+(?:\.\d+)?[kK]|IMM)", block.plain, re.I):
            continue
        element = color_element(block.color)
        if element is None:
            continue
        if not (label.x + 5 <= block.x <= label.x + 540):
            continue
        if not (label.y + 25 <= block.y <= label.y + 520):
            continue
        candidates.append((block, element))
    result: Dict[str, dict] = {}
    for row_index, row in enumerate(cluster_rows(candidates), start=1):
        values: Dict[str, object] = {}
        for block, element in row:
            values[element] = resistance_value(block.plain)
        if len(values) < 4:
            continue
        row_y = sum(item[0].y for item in row) / len(row)
        state_labels = [
            block
            for block in document.texts
            if block.visible
            and label.x - 120 <= block.x <= label.x + 90
            and abs(block.y - row_y) <= 28
            and block is not label
            and not re.fullmatch(r"-?\d+(?:\.\d+)?%?|IMM", block.plain, re.I)
        ]
        if state_labels:
            state_name = min(state_labels, key=lambda block: abs(block.y - row_y)).plain.replace("\n", "")
        else:
            state_name = "正常" if row_index == 1 else f"状态{row_index}"
        state = {"Name": state_name}
        for element in ELEMENT_ORDER:
            if element in values:
                state[element] = values[element]
        result[f"State{len(result) + 1}"] = state
        if len(values) != len(ELEMENT_ORDER):
            warnings.append(f"抗性状态“{state_name}”仅识别到 {len(values)}/8 个属性")
    if not result:
        warnings.append("未识别到有效抗性行")
    return result


def split_skill_header(value: str) -> Optional[Tuple[str, str]]:
    match = re.match(r"^(.+)[-－—]([^\n]{1,28})$", value.strip())
    if not match:
        return None
    name = match.group(1).strip()
    skill_type = re.sub(r"\s+", " ", match.group(2)).strip()
    if not name or not skill_type:
        return None
    if any(rejected in skill_type for rejected in SKILL_SUFFIX_REJECT):
        return None
    if re.search(r"[。；，,:：]", skill_type):
        return None
    if re.search(r"[。！？]$", name.rstrip("—－- ")) or re.search(r"[\[\]《》]", skill_type):
        return None
    if not re.search(r"[\w\u3400-\u9fff]", skill_type):
        return None
    return name, skill_type


def skill_headers(document: SourceDocument) -> List[Tuple[TextBlock, str, str]]:
    minimum_x = document.width * 0.25 if document.width else 500
    headers = []
    for block in document.texts:
        size = block.font_size or 0
        parsed = split_skill_header(block.plain)
        if not block.visible or parsed is None or not (34 <= size <= 52) or block.x < minimum_x:
            continue
        headers.append((block, parsed[0], parsed[1]))
    return sorted(headers, key=lambda item: (item[0].y, item[0].x))


def description_for_header(
    document: SourceDocument,
    header: TextBlock,
    all_headers: Sequence[Tuple[TextBlock, str, str]],
) -> Optional[TextBlock]:
    next_y = min(
        (
            other.y
            for other, _, _ in all_headers
            if other.y > header.y and abs(other.x - header.x) <= 120
        ),
        default=header.y + 320,
    )
    candidates = []
    for block in document.texts:
        size = block.font_size or 0
        if block is header or not block.visible or len(block.plain) < 5:
            continue
        if not (14 <= size <= 30):
            continue
        if not (header.y + 20 <= block.y < next_y):
            continue
        if abs(block.x - header.x) > 120:
            continue
        if block.plain.startswith(("制图@", "HP:", "ATK:", "Wt/Ed:")):
            continue
        score = abs(block.x - header.x) * 2 + (block.y - header.y)
        candidates.append((score, block))
    return min(candidates, key=lambda item: item[0])[1] if candidates else None


def matching_fxg_text(document: Optional[SourceDocument], block: TextBlock) -> Optional[TextBlock]:
    if document is None:
        return None
    candidates = []
    for candidate in document.texts:
        distance = abs(candidate.x - block.x) + abs(candidate.y - block.y)
        if distance > 24:
            continue
        similarity = difflib.SequenceMatcher(None, candidate.plain, block.plain).ratio()
        candidates.append((distance - similarity * 20, similarity, candidate))
    if not candidates:
        return None
    _, similarity, candidate = min(candidates, key=lambda item: item[0])
    return candidate if similarity >= 0.55 else None


def embedded_asset_path(document_path: Path, source: str) -> Optional[Path]:
    match = re.search(r"([^/'\"]+\.assets)/([^/'\"]+\.png)", source, re.I)
    if not match:
        return None
    assets_name = re.sub(r"%([0-9a-fA-F]{2})", lambda item: chr(int(item.group(1), 16)), match.group(1))
    assets_dir = document_path.parent / assets_name
    file_name = match.group(2)
    candidate = assets_dir / file_name
    if candidate.is_file():
        return candidate
    lower_name = file_name.casefold()
    return next(
        (path for path in assets_dir.glob("*.png") if path.name.casefold() == lower_name),
        None,
    )


def fxg_skill_icon(
    fxg: Optional[SourceDocument],
    heading_source: SourceDocument,
    header: TextBlock,
) -> Optional[Path]:
    if fxg is None:
        return None
    scale_x = fxg.width / heading_source.width if heading_source.width else 1
    scale_y = fxg.height / heading_source.height if heading_source.height else 1
    target_x = header.x * scale_x
    target_y = header.y * scale_y
    candidates = []
    for image in fxg.images:
        asset_path = embedded_asset_path(fxg.path, image.source)
        if asset_path is None:
            continue
        try:
            width, height = decoded_image_size(asset_path)
        except OSError:
            continue
        if not (110 <= width <= 140 and 110 <= height <= 140):
            continue
        horizontal = target_x - image.x
        vertical = abs((image.y + height / 2) - (target_y + 32 * scale_y))
        if not (80 <= horizontal <= 180 and vertical <= 45):
            continue
        score = abs(horizontal - 140) + vertical
        candidates.append((score, asset_path))
    return min(candidates, key=lambda item: item[0])[1] if candidates else None


def extract_skills(
    fxg: Optional[SourceDocument],
    mxml: Optional[SourceDocument],
    icon_hash_index: IconHashIndex,
    warnings: List[str],
) -> Tuple[List[dict], Set[Tuple[int, int]]]:
    heading_source = mxml or fxg
    if heading_source is None:
        return [], set()
    headers = skill_headers(heading_source)
    skills: List[dict] = []
    used_fxg_positions: Set[Tuple[int, int]] = set()
    for header, name, skill_type in headers:
        description_block = description_for_header(heading_source, header, headers)
        if description_block is None:
            warnings.append(f"技能“{name}”未匹配到描述")
            continue
        rich_block = matching_fxg_text(fxg, description_block)
        description = rich_block.rich if rich_block else description_block.rich
        if rich_block:
            used_fxg_positions.add((round(rich_block.x), round(rich_block.y)))
        source_icon = fxg_skill_icon(fxg, heading_source, header)
        icon_path = icon_hash_index.match(source_icon) if source_icon else None
        skill = {"Name": name, "Type": skill_type, "IconPath": icon_path or ""}
        skill["Description"] = description
        skills.append(skill)
    if not skills:
        warnings.append("未识别到技能标题；部分 FXG 的技能名仅存在于 MXML")
    return skills, used_fxg_positions


def contains_cjk(value: str) -> bool:
    return bool(re.search(r"[\u3400-\u9fff]", value))


def note_block_allowed(block: TextBlock, used_positions: Set[Tuple[int, int]]) -> bool:
    short_label = block.plain.endswith(("：", ":")) and len(block.plain) >= 2
    if not block.visible or (block.font_size or 999) > 20 or (len(block.plain) < 8 and not short_label):
        return False
    if (round(block.x), round(block.y)) in used_positions:
        return False
    if not contains_cjk(block.plain):
        return False
    rejected_prefixes = (
        "HP:",
        "ATK:",
        "Wt/Ed:",
        "Base HP",
        "Base ATK",
        "制图@",
        "元素能量",
    )
    if block.plain.startswith(rejected_prefixes) or " Lv." in block.plain:
        return False
    return True


def split_named_plain(value: str) -> List[Tuple[str, str]]:
    matches = list(re.finditer(r"(?:^|\n)([^：\n]{1,24})：", value))
    if not matches:
        return []
    result = []
    for index, match in enumerate(matches):
        name = match.group(1).strip()
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(value)
        description = value[start:end].strip()
        if name and description:
            result.append((name, description))
    return result


def rich_description(block: TextBlock, name: str, plain_description: str, split_count: int) -> str:
    if split_count != 1:
        return plain_description
    prefix = f"{name}："
    if block.rich.startswith(prefix):
        return block.rich[len(prefix) :].strip()
    return plain_description


def extract_notes(
    fxg: Optional[SourceDocument],
    mxml: Optional[SourceDocument],
    used_fxg_positions: Set[Tuple[int, int]],
) -> List[dict]:
    source = fxg or mxml
    if source is None:
        return []
    blocks = [block for block in source.texts if note_block_allowed(block, used_fxg_positions)]
    blocks.sort(key=lambda block: (block.y, block.x))
    consumed: Set[int] = set()
    notes: List[dict] = []
    labels = [
        (index, block)
        for index, block in enumerate(blocks)
        if block.plain.endswith(("：", ":")) and len(block.plain) <= 28
    ]
    for label_index, label in labels:
        candidates = []
        for body_index, body in enumerate(blocks):
            if body_index == label_index or body_index in consumed or body.y <= label.y:
                continue
            if body.y - label.y > 120 or abs(body.x - label.x) > 80 or len(body.plain) < 16:
                continue
            candidates.append((body.y - label.y + abs(body.x - label.x), body_index, body))
        if not candidates:
            continue
        _, body_index, body = min(candidates, key=lambda item: item[0])
        name = label.plain.rstrip("：:").strip()
        named_body = split_named_plain(body.plain)
        if named_body and named_body[0][0] == name:
            for body_name, description in named_body:
                notes.append(
                    {
                        "Text": body_name,
                        "Desc": rich_description(body, body_name, description, len(named_body)),
                    }
                )
        else:
            notes.append({"Text": name, "Desc": body.rich})
        consumed.update({label_index, body_index})
    mechanic_number = 0
    for index, block in enumerate(blocks):
        if index in consumed:
            continue
        named = split_named_plain(block.plain)
        if named:
            for name, description in named:
                notes.append(
                    {
                        "Text": name,
                        "Desc": rich_description(block, name, description, len(named)),
                    }
                )
            continue
        if len(block.plain) >= 20 and any(keyword in block.plain for keyword in MECHANIC_KEYWORDS):
            mechanic_number += 1
            name = "机制" if mechanic_number == 1 else f"机制{mechanic_number}"
            notes.append({"Text": name, "Desc": block.rich})
    deduplicated = []
    seen: Set[Tuple[str, str]] = set()
    for note in notes:
        key = (note["Text"], strip_markup(note["Desc"]))
        if key not in seen:
            seen.add(key)
            deduplicated.append(note)
    return deduplicated


def strip_markup(value: str) -> str:
    value = re.sub(r"</?(?:color(?:=[^>]+)?|u)>", "", value)
    return normalize_text(html.unescape(value))


def first_duration(texts: Iterable[str]) -> Optional[float]:
    values = list(texts)
    patterns = (
        r"(?:持续(?:时间)?|存在)\s*(\d+(?:\.\d+)?)\s*s\b",
        r"(?<![\d.])(\d+(?:\.\d+)?)\s*s后[^。；\n]{0,30}(?:爆炸|消失|结束|解除|恢复)",
    )
    for pattern in patterns:
        for value in values:
            match = re.search(pattern, value, re.I)
            if match:
                parsed = float(match.group(1))
                return int(parsed) if parsed.is_integer() else parsed
    return None


def extract_shields(notes: Sequence[dict], skills: Sequence[dict]) -> List[dict]:
    shields = []
    skill_plain = [strip_markup(skill.get("Description", "")) for skill in skills]
    for note in notes:
        name = note["Text"]
        description = note["Desc"]
        plain = strip_markup(description)
        combined = f"{name}：{plain}"
        explicit_gauge = re.search(r"\d+(?:\.\d+)?\s*(?:火|水|雷|岩|冰|冻|草|木|元素)?盾", combined)
        named_shield = any(keyword in name for keyword in ("盾", "护罩", "屏障", "护甲", "胄甲"))
        if not explicit_gauge and not named_shield:
            continue
        shield = {"Name": name, "Description": description, "IconPath": ""}
        if "深黯" in combined:
            shield["Type"] = "Damage"
            shield["ElementType"] = "Abyss"
        elif "次数盾" in combined or re.search(r"(?:承受|抵挡|需要)[^。；]{0,12}\d+次", combined):
            shield["Type"] = "Frequency"
        else:
            element_match = re.search(r"([火水雷岩冰冻草木])(?:元素)?盾", combined)
            if element_match:
                shield["Type"] = "Element"
                shield["ElementType"] = SHIELD_ELEMENTS[element_match.group(1)]
        related_skills = [text for text in skill_plain if name in text]
        duration = first_duration([plain, *related_skills])
        if duration is not None:
            shield["Duration"] = duration
        layer = re.search(r"(?:共|总计)?\s*(\d+)\s*层", combined)
        if layer:
            shield["ShieldLayer"] = int(layer.group(1))
        shields.append(shield)
    return shields


def extract_chargebars(notes: Sequence[dict]) -> List[dict]:
    chargebars = []
    for note in notes:
        name = note["Text"]
        description = note["Desc"]
        plain = strip_markup(description)
        combined = f"{name}：{plain}"
        subject = any(keyword in combined for keyword in ("充能条", "计量条", "热量", "怒气", "进度条"))
        behavior = any(keyword in combined for keyword in ("积攒", "充能", "充满", "上限", "总量", "进度"))
        if not subject or not behavior:
            continue
        chargebar = {"Name": name, "Description": description, "IconPath": ""}
        maximum = re.search(r"(?:上限|总量|最大值)\s*(\d+(?:\.\d+)?)(?![\d.]|\s*/)", plain)
        if maximum:
            parsed = float(maximum.group(1))
            chargebar["MaxValue"] = int(parsed) if parsed.is_integer() else parsed
        chargebar["Charge"] = [{"Type": "Other", "ConditionDesc": plain}]
        chargebars.append(chargebar)
    return chargebars


def source_category(path: Path) -> Tuple[str, str]:
    raw = re.sub(r"^(?:FXG|MXML)", "", path.parent.name, flags=re.I) or path.parent.name
    key = raw.lower()
    aliases = {"hilichurls": "hilichurl"}
    return aliases.get(key, key), raw


def discover_sources(fxg_root: Path, mxml_root: Path) -> List[MonsterSource]:
    records: Dict[Tuple[str, str], MonsterSource] = {}
    for kind, root, extension in (("fxg", fxg_root, ".fxg"), ("mxml", mxml_root, ".mxml")):
        if not root.exists():
            continue
        for path in root.rglob(f"*{extension}"):
            category_key, category_name = source_category(path)
            key = (category_key, path.stem)
            record = records.setdefault(key, MonsterSource(category=category_name, name=path.stem))
            setattr(record, kind, path)
            if kind == "fxg":
                record.category = category_name
    return sorted(records.values(), key=lambda item: (item.category.lower(), item.name))


def parse_document(path: Optional[Path], parser, warnings: List[str]) -> Optional[SourceDocument]:
    if path is None:
        return None
    try:
        return parser(path)
    except (ET.ParseError, OSError, ValueError) as error:
        warnings.append(f"解析 {path.as_posix()} 失败：{error}")
        return None


def load_json(path: Path, expected_type, label: str):
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"读取{label} {path.as_posix()} 失败：{error}") from error
    if not isinstance(value, expected_type):
        raise ValueError(f"{label} {path.as_posix()} 的顶层类型不正确")
    return value


def load_monster_catalog(path: Path) -> Tuple[List[dict], List[str]]:
    payload = load_json(path, list, "怪物目录数据")
    unique_entries = []
    duplicate_ids = []
    seen_ids: Set[str] = set()
    for raw_entry in payload:
        if not isinstance(raw_entry, dict):
            continue
        monster_id = raw_entry.get("InternalName")
        monster_name = raw_entry.get("DisplayName")
        if not isinstance(monster_id, str) or not monster_id:
            continue
        if not isinstance(monster_name, str) or not monster_name:
            continue
        if Path(monster_id).name != monster_id:
            raise ValueError(f"怪物目录包含非法 Id：{monster_id!r}")
        if monster_id in seen_ids:
            duplicate_ids.append(monster_id)
            continue
        seen_ids.add(monster_id)
        entry = dict(raw_entry)
        entry["Id"] = monster_id
        entry["NameZHCN"] = monster_name
        unique_entries.append(entry)
    return unique_entries, sorted(set(duplicate_ids))


def load_leyline_challenges(path: Path) -> List[dict]:
    payload = load_json(path, list, "幽境挑战数据")
    result = []
    for entry in payload:
        if not isinstance(entry, dict):
            continue
        required = ("Battle_CN", "MonsterId", "Monster", "Name5")
        if all(isinstance(entry.get(field), str) and entry[field] for field in required):
            result.append(entry)
    return result


def catalog_match_key(value: str) -> str:
    return identity_key(value).casefold()


def catalog_image_stem(entry: dict) -> str:
    image_path = entry.get("ImagePath")
    if not isinstance(image_path, str) or not image_path:
        return ""
    return catalog_match_key(Path(image_path).stem)


def leyline_ids_for_source(source: MonsterSource, challenges: Sequence[dict]) -> Set[str]:
    if source.category.lower() != "leyline":
        return set()
    source_key = catalog_match_key(source.name)
    result = set()
    for challenge in challenges:
        battle = catalog_match_key(f"{challenge['Battle_CN']}之役-")
        name5 = catalog_match_key(challenge["Name5"])
        full_name = catalog_match_key(
            f"{challenge['Battle_CN']}之役-{challenge['Monster']}·{challenge['Name5']}"
        )
        if source_key == full_name or (source_key.startswith(battle) and name5 in source_key):
            result.add(challenge["MonsterId"])
    return result


def catalog_entries_for_source(
    source: MonsterSource,
    entries: Sequence[dict],
    challenges: Sequence[dict],
) -> List[dict]:
    source_key = catalog_match_key(source.name)
    leyline_ids = leyline_ids_for_source(source, challenges)
    return [
        entry
        for entry in entries
        if entry["Id"] in leyline_ids
        or catalog_match_key(entry["NameZHCN"]) == source_key
        or catalog_image_stem(entry) == source_key
    ]


def catalog_match_score(
    source: MonsterSource,
    entry: dict,
    challenges: Sequence[dict],
) -> int:
    source_key = catalog_match_key(source.name)
    if catalog_match_key(entry["NameZHCN"]) == source_key:
        return 3
    if catalog_image_stem(entry) == source_key:
        return 2
    if entry["Id"] in leyline_ids_for_source(source, challenges):
        return 1
    return 0


def leyline_challenge_for_source(
    source: MonsterSource,
    entry: dict,
    challenges: Sequence[dict],
) -> Optional[dict]:
    if source.category.lower() != "leyline":
        return None
    candidates = [
        challenge
        for challenge in challenges
        if challenge.get("MonsterId") == entry.get("Id")
    ]
    if not candidates:
        return None
    source_key = catalog_match_key(source.name)
    same_battle = [
        challenge
        for challenge in candidates
        if source_key.startswith(catalog_match_key(f"{challenge['Battle_CN']}之役-"))
    ]
    return same_battle[0] if same_battle else candidates[0]


def split_leyline_state_name(value: object) -> Tuple[str, str]:
    if not isinstance(value, str):
        return "", ""
    match = re.fullmatch(r"\s*(.*?)\s*[（(]\s*(N\d+(?:\s*/\s*N\d+)*)\s*[)）]\s*", value, re.I)
    if not match:
        return value.strip(), ""
    return match.group(1).strip(), re.sub(r"\s+", "", match.group(2).upper())


def leyline_resistance(resistance: object, monster_id: str) -> Dict[str, dict]:
    if not isinstance(resistance, dict):
        return {}
    result: Dict[str, dict] = {}
    for state_id, raw_state in resistance.items():
        if not isinstance(raw_state, dict):
            continue
        state = copy.deepcopy(raw_state)
        name, diff_note = split_leyline_state_name(state.get("Name"))
        if monster_id == "Watcher_Primo_Leyline" and name == "记录":
            name = "监视"
        state["Name"] = name
        reordered = {"Name": name, "DiffNote": diff_note}
        for key, value in state.items():
            if key in ("Name", "DiffNote"):
                continue
            if (
                monster_id == "Watcher_Primo_Leyline"
                and state_id != "State1"
                and key == "Physical"
            ):
                continue
            reordered[key] = value
        result[state_id] = reordered
    return result


def leyline_tip_groups(value: object) -> Tuple[List[str], List[str]]:
    advantages: List[str] = []
    disadvantages: List[str] = []
    if isinstance(value, str):
        for raw_tag in value.split(","):
            tag = raw_tag.strip().upper()
            if not tag:
                continue
            is_disadvantage = tag.startswith("DIS_")
            base_tag = tag[4:] if is_disadvantage else tag
            mapped = LEYLINE_TIP_NAMES.get(base_tag, "".join(part.title() for part in base_tag.split("_")))
            destination = disadvantages if is_disadvantage else advantages
            if mapped and mapped not in destination:
                destination.append(mapped)
    return advantages, disadvantages or [""]


def leyline_coefficients(challenge: dict) -> Dict[str, object]:
    result: Dict[str, object] = {}
    for index in range(1, 7):
        value = challenge.get(f"n{index}")
        result[f"N{index}"] = value if isinstance(value, (int, float)) else ""
    return result


def watcher_chargebars(skills: Sequence[dict], notes: Sequence[dict]) -> List[dict]:
    note = next((item for item in notes if item.get("Text") == "监候之统摄"), None)
    burst = next((item for item in skills if item.get("Name") == "现象之谵妄"), None)
    note_plain = strip_markup(note.get("Desc", "")) if isinstance(note, dict) else ""
    burst_plain = strip_markup(burst.get("Description", "")) if isinstance(burst, dict) else ""
    required_values = ("3次", "9次", "5次", "15次", "5s", "8s")
    result: List[dict] = []
    if all(value in note_plain for value in required_values):
        description = (
            "记录期间的计量，分别记录<color=#ff9999>火</color>/"
            "<color=#80c0ff>水</color>/<color=#ffacff>雷</color>/"
            "<color=#99ffff>冰</color>的攻击次数，记录完成后将获得对应元素的监控状态"
        )
        charge = [
            {"Type": "ByElementType", "ElementType": element, "HitAddition": 1}
            for element in ("Fire", "Water", "Electric", "Ice")
        ]
        configurations = (
            ("高难度首次监控计量", 5, 5),
            ("高难度第二次监控计量", 8, 15),
            ("首次监控计量", 5, 3),
            ("第二次监控计量", 8, 9),
        )
        for name, duration, maximum in configurations:
            result.append(
                {
                    "Name": name,
                    "Description": description,
                    "IconPath": "static/images/UIIcon/cb-4n.png",
                    "ChargebarType": "4",
                    "Duration": duration,
                    "MaxValue": maximum,
                    "InitValue": 0,
                    "UseMultiplayerData": False,
                    "Charge": copy.deepcopy(charge),
                }
            )
    if "18s" in burst_plain:
        result.append(
            {
                "Name": "爆发蓄力倒计时",
                "Description": "现象之谵妄的蓄力期间计量，计量完成后将根据场上元素星数量召唤对应天谴之矛",
                "IconPath": "static/images/UIIcon/cb-17n.png",
                "ChargebarType": "17",
                "Duration": 18,
                "MaxValue": 18,
                "InitValue": 0,
                "UseMultiplayerData": False,
                "Charge": [
                    {
                        "Type": "ByTime",
                        "PeriodAddition": 0.3,
                        "AdditionInterval": 0.3,
                    }
                ],
            }
        )
    return result


def leyline_related_monster_ids(monster_id: str, entries: Sequence[dict]) -> List[str]:
    if monster_id == "Watcher_Primo_Leyline":
        expected = (
            "Watcher_Primo_ElementalStar_Same_Leyline",
            "Watcher_Primo_ElementalStar_Hard_Leyline",
        )
        available = {entry.get("Id") for entry in entries}
        return ["Watcher_Primo", *(item for item in expected if item in available)]
    return []


def build_leyline_fields(
    fields: dict,
    entry: dict,
    challenge: dict,
    catalog_entries: Sequence[dict],
) -> dict:
    monster_id = entry["Id"]
    name_zhcn = fields.get("NameZHCN", "")
    advantages, disadvantages = leyline_tip_groups(challenge.get("TipTags"))
    skills = copy.deepcopy(fields.get("Skill", []))
    for skill in skills:
        if isinstance(skill, dict):
            skill["DiffNote"] = ""
            ordered = {
                "Name": skill.get("Name", ""),
                "Type": skill.get("Type", ""),
                "DiffNote": skill.get("DiffNote", ""),
                "IconPath": skill.get("IconPath", ""),
                "Description": skill.get("Description", ""),
            }
            skill.clear()
            skill.update(ordered)
    notes = copy.deepcopy(fields.get("Notes", []))
    chargebar = copy.deepcopy(fields.get("Chargebar", []))
    if monster_id == "Watcher_Primo_Leyline":
        chargebar = watcher_chargebars(skills, notes)
    return {
        "NameZHCN": name_zhcn,
        "NameENUS": fields.get("NameENUS", ""),
        "SubtitleZHCN": fields.get("SubtitleZHCN", ""),
        "SubtitleENUS": fields.get("SubtitleENUS", ""),
        "Description": fields.get("Description", ""),
        "NormName": f"{challenge.get('Monster', '')}·常形" if challenge.get("Monster") else "",
        "HardName": (
            f"{challenge.get('Monster', '')}·{str(challenge.get('Name4', '')).strip()}"
            if challenge.get("Monster") and str(challenge.get("Name4", "")).strip()
            else ""
        ),
        "DireName": name_zhcn,
        "FirstCampaign": f"{challenge.get('Battle_CN', '')}之役" if challenge.get("Battle_CN") else "",
        "Tier": catalog_tier(entry.get("Tier")),
        "Category": catalog_category(entry.get("Category")),
        "RelatedMonsterIds": leyline_related_monster_ids(monster_id, catalog_entries),
        "Resistace": leyline_resistance(fields.get("Resistace"), monster_id),
        "EnergyDrop": copy.deepcopy(fields.get("EnergyDrop", {"Type": "", "EnergyGroup": ""})),
        "BaseHp": copy.deepcopy(fields.get("BaseHp", {"Value": "", "Curve": ""})),
        "BaseAtk": copy.deepcopy(fields.get("BaseAtk", {"Value": "", "Curve": ""})),
        "BaseDef": copy.deepcopy(fields.get("BaseDef", {"Value": 500, "Curve": "DEF_1"})),
        "Coefficients": leyline_coefficients(challenge),
        "DefaultLevels": copy.deepcopy(LEYLINE_DEFAULT_LEVELS),
        "ElementMastery": fields.get("ElementMastery", 0),
        "MultiplayerGroup": fields.get("MultiplayerGroup", ""),
        "Advantages": advantages,
        "Disadvantages": disadvantages,
        "SkillDiffer": skills,
        "MechanismNotes": notes,
        "Shield": copy.deepcopy(fields.get("Shield", [])),
        "Chargebar": chargebar,
    }


def apply_catalog_entry(
    payload: list,
    entry: dict,
    challenge: Optional[dict] = None,
    catalog_entries: Sequence[dict] = (),
) -> list:
    fields = copy.deepcopy(next(iter(payload[0].values())))
    monster_id = entry["Id"]
    fields["Id"] = monster_id
    fields["Tier"] = catalog_tier(entry.get("Tier"))
    fields["Category"] = catalog_category(entry.get("Category"))
    if challenge is None:
        fields["NameZHCN"] = entry["NameZHCN"]
    multiplayer_group = entry.get("MultiplayerGroup")
    if isinstance(multiplayer_group, (int, str)):
        fields["MultiplayerGroup"] = str(multiplayer_group)
    base_hp = entry.get("BaseHp")
    hp_curve = entry.get("HpCurve")
    if isinstance(base_hp, (int, float)) and isinstance(hp_curve, str):
        fields["BaseHp"] = {"Value": base_hp, "Curve": hp_curve}
    base_atk = entry.get("BaseAtk")
    atk_curve = entry.get("AtkCurve")
    if isinstance(base_atk, (int, float)) and isinstance(atk_curve, str):
        fields["BaseAtk"] = {"Value": base_atk, "Curve": atk_curve}
    if challenge is not None:
        fields = build_leyline_fields(fields, entry, challenge, catalog_entries)
    return [{monster_id: fields}]


def catalog_tier(tier: object) -> str:
    aliases = {"Weekly": "Boss", "Leyline": "Boss"}
    return aliases.get(tier, tier) if isinstance(tier, str) else ""


def catalog_category(category: object) -> str:
    aliases = {
        "EnemiesOfNote": "EnemyOfNote",
        "LeylineChallenge": "StygianOnslaught",
        "MysticalBeasts": "MagicalBeasts",
    }
    return aliases.get(category, category) if isinstance(category, str) else ""


def empty_catalog_payload(entry: dict) -> list:
    monster_id = entry["Id"]
    base_hp = entry.get("BaseHp")
    hp_curve = entry.get("HpCurve")
    base_atk = entry.get("BaseAtk")
    atk_curve = entry.get("AtkCurve")
    multiplayer_group = entry.get("MultiplayerGroup")
    fields = {
        "NameZHCN": entry["NameZHCN"],
        "NameENUS": "",
        "Id": monster_id,
        "SubtitleZHCN": "",
        "SubtitleENUS": "",
        "Description": "",
        "Tier": catalog_tier(entry.get("Tier")),
        "Category": catalog_category(entry.get("Category")),
        "Resistace": {},
        "EnergyDrop": {"Type": "", "EnergyGroup": ""},
        "BaseHp": {
            "Value": base_hp if isinstance(base_hp, (int, float)) else "",
            "Curve": hp_curve if isinstance(hp_curve, str) else "",
        },
        "BaseAtk": {
            "Value": base_atk if isinstance(base_atk, (int, float)) else "",
            "Curve": atk_curve if isinstance(atk_curve, str) else "",
        },
        "BaseDef": {"Value": 500, "Curve": "DEF_1"},
        "DefaultLevel": "",
        "ElementMastery": 0,
        "MultiplayerGroup": str(multiplayer_group) if multiplayer_group is not None else "",
        "Weight": "",
        "EndureType": "",
        "Skill": [],
        "Notes": [],
        "Shield": [],
        "Chargebar": [],
    }
    return [{monster_id: fields}]


def load_reference_catalog(paths: Sequence[Path]) -> ReferenceCatalog:
    catalog = ReferenceCatalog()
    collections = (
        ("Skill", catalog.skills),
        ("SkillDiffer", catalog.skills),
        ("Shield", catalog.shields),
        ("Chargebar", catalog.chargebars),
    )
    for path in paths:
        payload = load_json(path, list, "图标参考 JSON")
        for wrapper in payload:
            if not isinstance(wrapper, dict):
                continue
            for internal_name, monster in wrapper.items():
                if not isinstance(monster, dict):
                    continue
                if not isinstance(internal_name, str) or Path(internal_name).name != internal_name:
                    raise ValueError(
                        f"图标参考 JSON {path.as_posix()} 包含非法外层索引：{internal_name!r}"
                    )
                monster_name = monster.get("NameZHCN") or internal_name
                if not isinstance(monster_name, str) or not monster_name:
                    continue
                monster_id = monster.get("Id") or internal_name
                if isinstance(internal_name, str) and isinstance(monster_id, str):
                    catalog.identifiers[monster_name] = (internal_name, monster_id)
                for field_name, destination in collections:
                    items = monster.get(field_name, [])
                    if not isinstance(items, list):
                        continue
                    for item in items:
                        if not isinstance(item, dict):
                            continue
                        item_name = item.get("Name")
                        icon_path = item.get("IconPath")
                        if (
                            isinstance(item_name, str)
                            and item_name
                            and isinstance(icon_path, str)
                            and icon_path
                        ):
                            destination[(monster_name, item_name)] = icon_path
    return catalog


def resolve_identifiers(
    monster_names: Sequence[str],
    catalog: ReferenceCatalog,
) -> Optional[Tuple[str, str]]:
    return next(
        (
            catalog.identifiers[monster_name]
            for monster_name in monster_names
            if monster_name in catalog.identifiers
        ),
        None,
    )


def apply_icon_catalog(
    monster_names: Sequence[str],
    items: Sequence[dict],
    icon_paths: Dict[Tuple[str, str], str],
    overwrite: bool = True,
) -> int:
    applied = 0
    for item in items:
        item_name = item.get("Name")
        if not isinstance(item_name, str):
            continue
        icon_path = next(
            (
                icon_paths[(monster_name, item_name)]
                for monster_name in monster_names
                if (monster_name, item_name) in icon_paths
            ),
            "",
        )
        applied_icon = bool(icon_path and (overwrite or not item.get("IconPath")))
        if applied_icon:
            item["IconPath"] = icon_path
        applied += int(applied_icon)
    return applied


def primary_document(
    fxg: Optional[SourceDocument],
    mxml: Optional[SourceDocument],
) -> Optional[SourceDocument]:
    return mxml or fxg


def first_matching_text(document: Optional[SourceDocument], pattern: str) -> Optional[str]:
    if document is None:
        return None
    regex = re.compile(pattern, re.I)
    for block in document.texts:
        if block.visible and regex.search(block.plain):
            return block.plain
    return None


def identity_key(value: str) -> str:
    return re.sub(r"[\s「」『』《》]", "", value)


def latin_identity_block(block: TextBlock, maximum_x: Optional[float] = 600) -> bool:
    if (
        not block.visible
        or (maximum_x is not None and block.x > maximum_x)
        or not re.search(r"[A-Za-z]", block.plain)
    ):
        return False
    if not (18 <= (block.font_size or 0) <= 32) or len(block.plain) > 160:
        return False
    rejected = ("HP:", "ATK:", "Wt/Ed:", "SHD:", "Base HP", "Base ATK", "制图@")
    return not block.plain.startswith(rejected) and "Lv." not in block.plain and " Data" not in block.plain


def extract_identity(document: Optional[SourceDocument], source_name: str) -> dict:
    result = {
        "NameZHCN": source_name,
        "NameENUS": "",
        "SubtitleZHCN": "",
        "SubtitleENUS": "",
    }
    if document is None:
        return result
    possible_titles = {
        identity_key(source_name),
        identity_key(re.sub(r"\d+$", "", source_name)),
    }
    title = next(
        (
            block
            for block in document.texts
            if block.visible
            and any(
                identity_key(block.plain) == candidate
                or (
                    identity_key(block.plain).startswith(candidate)
                    and len(identity_key(block.plain)) - len(candidate) <= 2
                )
                for candidate in possible_titles
                if candidate
            )
        ),
        None,
    )
    if title is not None:
        nearby = [
            block
            for block in document.texts
            if block.visible
            and title.y < block.y <= title.y + 260
            and abs(block.x - title.x) <= 100
            and len(block.plain) <= 100
            and not block.plain.startswith(("HP:", "ATK:", "Wt/Ed:", "制图@"))
            and " Data" not in block.plain
        ]
        chinese = [block for block in nearby if contains_cjk(block.plain)]
        if chinese:
            result["SubtitleZHCN"] = min(chinese, key=lambda block: block.y).plain
    latin = [block for block in document.texts if latin_identity_block(block)]
    if title is not None:
        close_latin = [
            block
            for block in latin
            if title.y < block.y <= title.y + 320 and abs(block.x - title.x) <= 100
        ]
        latin = close_latin or latin
    latin.sort(key=lambda block: block.y)
    if latin:
        result["NameENUS"] = latin[0].plain
    if len(latin) > 1:
        result["SubtitleENUS"] = latin[1].plain
    return result


def extract_leyline_identity(
    document: Optional[SourceDocument], source_name: str
) -> dict:
    result = {
        "NameZHCN": re.sub(r"^.+?之役-", "", source_name),
        "NameENUS": "",
        "SubtitleZHCN": "",
        "SubtitleENUS": "",
    }
    if document is None:
        return result
    titles = [
        block
        for block in document.texts
        if block.visible
        and block.y <= 180
        and (block.font_size or 0) >= 60
        and contains_cjk(block.plain)
        and not block.plain.startswith("制图@")
    ]
    if titles:
        title = min(titles, key=lambda block: (block.y, -float(block.font_size or 0)))
        result["NameZHCN"] = title.plain
        latin = [
            block
            for block in document.texts
            if latin_identity_block(block, maximum_x=None)
            and title.y < block.y <= title.y + 180
            and abs(block.x - title.x) <= 420
        ]
        if latin:
            result["NameENUS"] = min(latin, key=lambda block: block.y).plain
    return result


def extract_level_and_tier(
    document: Optional[SourceDocument], warnings: List[str]
) -> Tuple[object, str]:
    value = first_matching_text(document, r"Lv\.\s*\d+\s*Data")
    if value is None:
        warnings.append("未识别到默认等级和怪物等级类型")
        return "", ""
    level_match = re.search(r"Lv\.\s*(\d+)", value, re.I)
    level: object = int(level_match.group(1)) if level_match else ""
    tier = next((mapped for keyword, mapped in TIER_KEYWORDS if keyword in value), "")
    if not tier:
        warnings.append(f"未能从“{value.replace(chr(10), ' ')}”识别怪物等级类型")
    return level, tier


def rounded_base_value(value: float, stat_name: str):
    digits = 3 if stat_name == "HP" else 2
    rounded = round(value, digits)
    return int(rounded) if rounded.is_integer() else rounded


def extract_base_stat(
    document: Optional[SourceDocument],
    stat_name: str,
    level: object,
    curve_data: Dict[str, dict],
    warnings: List[str],
) -> dict:
    text = first_matching_text(document, rf"\b{stat_name}\s*:")
    result = {"Value": "", "Curve": ""}
    if text is None:
        warnings.append(f"未找到 {stat_name} 数据")
        return result
    displayed_match = re.search(
        rf"\b{stat_name}\s*:\s*(?:\d+(?:\.\d+)?x)?(\d+(?:\.\d+)?)",
        text,
        re.I,
    )
    if stat_name == "HP":
        curve_match = re.search(r"\([^)]*?\d+(?:\.\d+)?\s*([HFP])\b", text, re.I)
        curve_code = curve_match.group(1).upper() if curve_match else None
    else:
        curve_match = re.search(r"\([^)]*?\d+(?:\.\d+)?\s*(B[012])\b", text, re.I)
        curve_code = curve_match.group(1).upper() if curve_match else None
    curve = CURVE_CODES.get(curve_code or "")
    if curve is None:
        warnings.append(f"未能从“{text.replace(chr(10), ' ')}”识别 {stat_name} 曲线")
        return result
    result["Curve"] = curve[0]
    if not displayed_match:
        warnings.append(f"未能从“{text.replace(chr(10), ' ')}”读取 {stat_name} 数值")
        return result
    if not isinstance(level, int):
        warnings.append(f"缺少等级，无法还原 {stat_name} 基础值")
        return result
    level_curves = curve_data.get(str(level))
    factor = level_curves.get(curve[1]) if isinstance(level_curves, dict) else None
    if not isinstance(factor, (int, float)) or factor == 0:
        warnings.append(f"曲线表缺少 Lv.{level} 的 {curve[1]}")
        return result
    displayed = float(displayed_match.group(1))
    result["Value"] = rounded_base_value(displayed / factor, stat_name)
    return result


def extract_weight(document: Optional[SourceDocument], warnings: List[str]):
    text = first_matching_text(document, r"Wt/Ed\s*:")
    if text is None:
        warnings.append("未找到 Wt/Ed 数据")
        return ""
    match = re.search(r"Wt/Ed\s*:\s*(\d+(?:\.\d+)?)\s*/", text, re.I)
    if not match:
        warnings.append(f"未能从“{text.replace(chr(10), ' ')}”读取重量")
        return ""
    parsed = float(match.group(1))
    return int(parsed) if parsed.is_integer() else parsed


def numeric_list(value) -> List[float]:
    if isinstance(value, str):
        parts = [part.strip().rstrip("%") for part in value.split(",")]
    elif isinstance(value, (int, float)):
        parts = [value]
    else:
        return []
    result = []
    for part in parts:
        try:
            parsed = float(part)
        except (TypeError, ValueError):
            return []
        result.append(int(parsed) if parsed.is_integer() else parsed)
    return result


def energy_signature(
    row: dict,
) -> Optional[Tuple[Tuple[float, ...], Tuple[float, ...], Tuple[float, ...]]]:
    modes = numeric_list(row.get("DropMode"))
    particles = numeric_list(row.get("ParticleDrop"))
    orbs = numeric_list(row.get("OrbDrop"))
    if not modes or len(modes) != len(particles) or len(modes) != len(orbs):
        return None
    return tuple(modes), tuple(particles), tuple(orbs)


def build_energy_index(
    rows: Sequence[dict],
) -> Dict[Tuple[Tuple[float, ...], Tuple[float, ...], Tuple[float, ...]], str]:
    result = {}
    for row in rows:
        signature = energy_signature(row)
        group = row.get("EnergyGroup")
        if signature is not None and isinstance(group, str):
            result[signature] = group
    return result


def first_energy_variant(value: str) -> str:
    value = value.replace("；", ";").strip()
    value = re.split(r"[;\n\r]", value, maxsplit=1)[0].strip()
    value = re.sub(r"([PHECDAGW][PO])/[PHECDAGW][PO](?=\[)", r"\1", value)
    return value


def parse_energy_schedule(
    value: str,
) -> Optional[Tuple[str, Tuple[Tuple[float, ...], Tuple[float, ...], Tuple[float, ...]]]]:
    tokens = re.findall(r"([PHECDAGW])([PO])\[([^\]]*)\]", value, re.I)
    if not tokens:
        return None
    element_code = tokens[0][0].upper()
    particles: Dict[float, float] = {}
    orbs: Dict[float, float] = {}
    thresholds: Set[float] = set()
    for token_element, drop_type, contents in tokens:
        if token_element.upper() != element_code:
            continue
        destination = particles if drop_type.upper() == "P" else orbs
        for item in contents.split(","):
            match = re.fullmatch(
                r"\s*(\d+(?:\.\d+)?)(?:\s*x\s*(\d+(?:\.\d+)?))?\s*",
                item,
                re.I,
            )
            if not match:
                return None
            threshold = float(match.group(1))
            count = float(match.group(2) or 1)
            threshold = int(threshold) if threshold.is_integer() else threshold
            count = int(count) if count.is_integer() else count
            thresholds.add(threshold)
            destination[threshold] = destination.get(threshold, 0) + count
    ordered = tuple(sorted(thresholds, reverse=True))
    signature = (
        ordered,
        tuple(particles.get(threshold, 0) for threshold in ordered),
        tuple(orbs.get(threshold, 0) for threshold in ordered),
    )
    return ENERGY_TYPES[element_code], signature


def extract_energy_drop(
    document: Optional[SourceDocument],
    energy_index: Dict[Tuple[Tuple[float, ...], Tuple[float, ...], Tuple[float, ...]], str],
    warnings: List[str],
) -> dict:
    result = {"Type": "", "EnergyGroup": ""}
    text = first_matching_text(document, r"元素能量\s*[：:]|Energy\s+Drop")
    if text is None:
        warnings.append("未找到元素能量数据")
        return result
    if re.search(r"元素能量\s*[：:]", text, re.I):
        raw = re.split(r"元素能量\s*[：:]", text, maxsplit=1, flags=re.I)[-1].strip()
    else:
        raw = re.split(r"Energy\s+Drop", text, maxsplit=1, flags=re.I)[0].strip()
    variant = first_energy_variant(raw)
    if variant.upper().startswith("NA"):
        result.update({"Type": "None", "EnergyGroup": "N_0000"})
        return result
    parsed = parse_energy_schedule(variant)
    if parsed is None:
        warnings.append(f"未能解析元素能量表达式“{variant}”")
        return result
    energy_type, signature = parsed
    result["Type"] = energy_type
    group = energy_index.get(signature)
    if group is None:
        warnings.append(f"元素能量表达式“{variant}”未匹配到 EnergyGroup")
    else:
        result["EnergyGroup"] = group
    return result


def normalized_category(category: str) -> str:
    return CATEGORY_ALIASES.get(category, category)


def generate_fragment(
    source: MonsterSource,
    curve_data: Dict[str, dict],
    energy_index: Dict[Tuple[Tuple[float, ...], Tuple[float, ...], Tuple[float, ...]], str],
    icon_hash_index: IconHashIndex,
    reference_catalog: ReferenceCatalog,
) -> Tuple[list, dict]:
    warnings: List[str] = []
    fxg = parse_document(source.fxg, parse_fxg, warnings)
    mxml = parse_document(source.mxml, parse_mxml, warnings)
    if source.fxg is None:
        warnings.append("没有配对的 FXG 文件")
    if source.mxml is None:
        warnings.append("没有配对的 MXML 文件")
    resistance = extract_resistance(fxg, warnings)
    skills, used_positions = extract_skills(fxg, mxml, icon_hash_index, warnings)
    notes = extract_notes(fxg, mxml, used_positions)
    shields = extract_shields(notes, skills)
    chargebars = extract_chargebars(notes)
    data_document = primary_document(fxg, mxml)
    is_leyline = source.category.lower() == "leyline"
    if is_leyline:
        default_level, tier = "", "Boss"
        base_hp = {"Value": "", "Curve": ""}
        base_atk = {"Value": "", "Curve": ""}
    else:
        default_level, tier = extract_level_and_tier(data_document, warnings)
        base_hp = extract_base_stat(data_document, "HP", default_level, curve_data, warnings)
        base_atk = extract_base_stat(data_document, "ATK", default_level, curve_data, warnings)
    energy_drop = extract_energy_drop(data_document, energy_index, warnings)
    identity = (
        extract_leyline_identity(data_document, source.name)
        if is_leyline
        else extract_identity(data_document, source.name)
    )
    monster_names = tuple(dict.fromkeys((identity["NameZHCN"], source.name)))
    identifiers = resolve_identifiers(monster_names, reference_catalog)
    if identifiers is None:
        outer_index = source.name
        monster_id = ""
    else:
        outer_index, monster_id = identifiers
    hash_icon_count = sum(bool(skill.get("IconPath")) for skill in skills)
    reference_icon_count = sum(
        (
            apply_icon_catalog(monster_names, skills, reference_catalog.skills, overwrite=False),
            apply_icon_catalog(monster_names, shields, reference_catalog.shields),
            apply_icon_catalog(monster_names, chargebars, reference_catalog.chargebars),
        )
    )
    fields = {
        "NameZHCN": identity["NameZHCN"],
        "NameENUS": identity["NameENUS"],
        "Id": monster_id,
        "SubtitleZHCN": identity["SubtitleZHCN"],
        "SubtitleENUS": identity["SubtitleENUS"],
        "Description": "",
        "Tier": tier,
        "Category": normalized_category(source.category),
        "Resistace": resistance,
        "EnergyDrop": energy_drop,
        "BaseHp": base_hp,
        "BaseAtk": base_atk,
        "BaseDef": {"Value": 500, "Curve": "DEF_1"},
        "DefaultLevel": default_level,
        "ElementMastery": 0,
        "MultiplayerGroup": "1",
        "Weight": "" if is_leyline else extract_weight(data_document, warnings),
        "EndureType": "",
        "Skill": skills,
        "Notes": notes,
        "Shield": shields,
        "Chargebar": chargebars,
    }
    payload = [{outer_index: fields}]
    report = {
        "name": source.name,
        "index": outer_index,
        "category": source.category,
        "fxg": source.fxg.as_posix() if source.fxg else None,
        "mxml": source.mxml.as_posix() if source.mxml else None,
        "counts": {
            "resistance_states": len(resistance),
            "skills": len(skills),
            "notes": len(notes),
            "shields": len(shields),
            "chargebars": len(chargebars),
            "base_stats": sum(field["Value"] != "" for field in (base_hp, base_atk)),
            "energy_group": int(bool(energy_drop["EnergyGroup"])),
            "identifier": int(identifiers is not None),
            "icon_paths": sum(bool(item.get("IconPath")) for item in (*skills, *shields, *chargebars)),
            "hash_skill_icons": hash_icon_count,
            "reference_icons": reference_icon_count,
        },
        "missing_fields": ["identifier"] if identifiers is None else [],
        "warnings": warnings,
    }
    return payload, report


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def path_is_inside(path: Path, directory: Path) -> bool:
    try:
        path.relative_to(directory)
        return True
    except ValueError:
        return False


def collect_json_files(
    source_root: Path,
    destination: Path,
    overwrite: bool = False,
) -> Tuple[int, int]:
    source_root = source_root.resolve()
    destination = destination.resolve()
    if not source_root.is_dir():
        raise ValueError(f"JSON 来源目录不存在：{source_root.as_posix()}")
    if source_root == destination:
        raise ValueError("JSON 来源目录和汇总目录不能相同")
    sources = [
        path
        for path in source_root.rglob("*.json")
        if not path.name.startswith("_")
        and not path_is_inside(path.resolve(), destination)
    ]
    targets: Dict[Path, Path] = {}
    for source in sorted(sources):
        target = destination / source.name
        previous = targets.get(target)
        if previous is not None:
            raise ValueError(
                "扁平汇总出现同名 JSON："
                f"{previous.as_posix()} 和 {source.as_posix()}"
            )
        targets[target] = source
    destination.mkdir(parents=True, exist_ok=True)
    if overwrite:
        for existing in destination.glob("*.json"):
            existing.unlink()
    copied = 0
    skipped = 0
    for target, source in targets.items():
        if target.exists() and not overwrite:
            skipped += 1
            continue
        shutil.copy2(source, target)
        copied += 1
    return copied, skipped


def clear_generated_json(output_root: Path) -> int:
    if not output_root.exists():
        return 0
    removed = 0
    for path in output_root.rglob("*.json"):
        path.unlink()
        removed += 1
    return removed


def collect_and_report(source_root: Path, destination: Path, overwrite: bool) -> int:
    try:
        copied, skipped = collect_json_files(source_root, destination, overwrite)
    except (OSError, ValueError) as error:
        print(f"汇总 JSON 失败：{error}", file=sys.stderr)
        return 1
    print(f"汇总 JSON：复制 {copied}，跳过 {skipped}。")
    print(f"汇总目录：{destination.as_posix()}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="遍历 FXG/MXML，为每个怪物生成完整格式的 JSON。"
    )
    parser.add_argument("--fxg-root", type=Path, default=Path("fxg"), help="FXG 根目录")
    parser.add_argument("--mxml-root", type=Path, default=Path("mxml"), help="MXML 根目录")
    parser.add_argument(
        "--curve-data",
        type=Path,
        default=Path("MonsterCurveExcelConfigData.json"),
        help="怪物等级曲线 JSON",
    )
    parser.add_argument(
        "--energy-data",
        type=Path,
        default=Path("MonsterEnergyDropExcelData.json"),
        help="怪物能量掉落 JSON",
    )
    parser.add_argument(
        "--catalog-data",
        type=Path,
        default=Path("MonsterCatalogExcelData.json"),
        help="怪物目录 JSON，InternalName 用作文件名和外层索引",
    )
    parser.add_argument(
        "--leyline-data",
        type=Path,
        default=Path("LeylineChallengeLevelExcelData.json"),
        help="幽境挑战等级 JSON，用于匹配挑战源文件和 MonsterId",
    )
    parser.add_argument(
        "--icons-root",
        type=Path,
        default=Path("icons"),
        help="运行时图标目录，以 RGBA 像素哈希匹配 FXG 的 image_N.png",
    )
    parser.add_argument(
        "--reference-json",
        "--icon-reference",
        dest="reference_json",
        type=Path,
        action="append",
        default=[Path("Magbeast_Gecko.json"), Path("Watcher_Primo_Leyline.json")],
        help="已验证的怪物 JSON，可重复传入以复用内部标识和 IconPath",
    )
    parser.add_argument("--output", type=Path, default=Path("generated_json"), help="输出目录")
    parser.add_argument(
        "--collect-output",
        type=Path,
        default=Path("collected_json"),
        help="将输出目录内所有怪物 JSON 扁平复制到此目录",
    )
    parser.add_argument(
        "--no-collect",
        action="store_const",
        dest="collect_output",
        const=None,
        help="生成后不创建扁平 JSON 汇总目录",
    )
    parser.add_argument(
        "--collect-only",
        action="store_true",
        help="不生成怪物，只汇总现有输出目录中的 JSON",
    )
    parser.add_argument("--include", help="只处理名称或分类中包含此文本的怪物")
    parser.add_argument("--limit", type=int, help="最多处理多少个怪物，便于抽样检查")
    parser.add_argument("--overwrite", action="store_true", help="覆盖已经生成的 JSON")
    parser.add_argument(
        "--clean-output",
        action="store_true",
        help="生成前删除输出目录中的旧 JSON，避免遗留旧文件名",
    )
    parser.add_argument("--dry-run", action="store_true", help="解析但不写入文件")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    if args.collect_only:
        if args.collect_output is None:
            print("--collect-only 必须与 --collect-output 一起使用", file=sys.stderr)
            return 1
        return collect_and_report(args.output, args.collect_output, args.overwrite)
    if args.clean_output and (args.include or args.limit is not None):
        print("--clean-output 不能与 --include 或 --limit 一起使用", file=sys.stderr)
        return 1
    try:
        curve_data = load_json(args.curve_data, dict, "怪物曲线数据")
        energy_rows = load_json(args.energy_data, list, "怪物能量掉落数据")
        catalog_entries, duplicate_catalog_ids = load_monster_catalog(args.catalog_data)
        leyline_challenges = load_leyline_challenges(args.leyline_data)
        icon_hash_index = IconHashIndex.build(args.icons_root)
        reference_catalog = load_reference_catalog(args.reference_json)
    except ValueError as error:
        print(error, file=sys.stderr)
        return 1
    energy_index = build_energy_index(energy_rows)
    if icon_hash_index.unreadable_files:
        print(
            f"图标目录中有 {len(icon_hash_index.unreadable_files)} 张 PNG 无法解码。",
            file=sys.stderr,
        )
    sources = discover_sources(args.fxg_root, args.mxml_root)
    if args.include:
        sources = [source for source in sources if args.include in source.name or args.include in source.category]
    if args.limit is not None:
        sources = sources[: max(0, args.limit)]
    if not sources:
        print("没有找到符合条件的 FXG/MXML 文件。", file=sys.stderr)
        return 1
    reports = []
    selected_records: Dict[str, Tuple[int, Path, list, dict]] = {}
    for source in sources:
        payload, report = generate_fragment(
            source,
            curve_data,
            energy_index,
            icon_hash_index,
            reference_catalog,
        )
        matches = catalog_entries_for_source(source, catalog_entries, leyline_challenges)
        report["catalog_candidates"] = [entry["Id"] for entry in matches]
        report["catalog_ids"] = []
        report["counts"]["identifier"] = int(bool(matches))
        report["counts"]["catalog_ids"] = len(matches)
        report["missing_fields"] = [] if matches else ["identifier"]
        report["outputs"] = []
        for entry in matches:
            monster_id = entry["Id"]
            output_path = args.output / entry["Category"] / f"{entry['Id']}.json"
            challenge = leyline_challenge_for_source(source, entry, leyline_challenges)
            catalog_payload = apply_catalog_entry(
                payload,
                entry,
                challenge,
                catalog_entries,
            )
            score = catalog_match_score(source, entry, leyline_challenges)
            previous = selected_records.get(monster_id)
            if previous is None or score > previous[0]:
                selected_records[monster_id] = (score, output_path, catalog_payload, report)
        reports.append(report)
    matched_catalog_id_count = len(selected_records)
    placeholder_entries = []
    if not args.include and args.limit is None:
        placeholder_entries = [
            entry for entry in catalog_entries if entry["Id"] not in selected_records
        ]
    for entry in placeholder_entries:
        output_path = args.output / entry["Category"] / f"{entry['Id']}.json"
        selected_records[entry["Id"]] = (
            0,
            output_path,
            empty_catalog_payload(entry),
            None,
        )
    records = []
    for monster_id, (_, output_path, catalog_payload, report) in selected_records.items():
        if report is not None:
            report["catalog_ids"].append(monster_id)
            report["outputs"].append(output_path.as_posix())
        records.append((output_path, catalog_payload))
    catalog_coverage = {
        "unique_ids": len(catalog_entries),
        "generated_from_sources": matched_catalog_id_count,
        "complete_catalog": not args.include and args.limit is None,
        "placeholder_ids": [entry["Id"] for entry in placeholder_entries],
        "duplicate_ids": duplicate_catalog_ids,
        "unmatched_sources": [
            report["name"] for report in reports if not report["catalog_candidates"]
        ],
    }
    written = 0
    skipped = 0
    removed = 0
    if not args.dry_run:
        if args.clean_output:
            try:
                removed = clear_generated_json(args.output)
            except OSError as error:
                print(f"清理旧 JSON 失败：{error}", file=sys.stderr)
                return 1
        for output_path, payload in records:
            if output_path.exists() and not args.overwrite:
                skipped += 1
                continue
            write_json(output_path, payload)
            written += 1
        write_json(args.output / "_generation_report.json", reports)
        write_json(args.output / "_catalog_coverage_report.json", catalog_coverage)
    warning_count = sum(len(report["warnings"]) for report in reports)
    missing_identifier_count = sum(
        report["counts"]["identifier"] == 0 for report in reports
    )
    print(
        f"处理 {len(reports)} 个图表源、匹配 {len(records)} 个 Catalog Id："
        f"写入 {written}，跳过 {skipped}，"
        f"清理旧文件 {removed}，报告警告 {warning_count}，"
        f"未匹配图表源 {missing_identifier_count}，Catalog 占位 {len(placeholder_entries)}。"
    )
    if duplicate_catalog_ids:
        print(
            f"MonsterCatalogExcelData.json 含 {len(duplicate_catalog_ids)} 个重复 Id，"
            "已按首次出现的条目生成。"
        )
    print(
        f"图标哈希索引：{icon_hash_index.file_count} 张 PNG，"
        f"{len(icon_hash_index.paths_by_hash)} 个唯一哈希，"
        f"{icon_hash_index.duplicate_hash_count} 组重复哈希。"
    )
    if not args.dry_run:
        print(f"输出目录：{args.output.as_posix()}")
        print(f"检查报告：{(args.output / '_generation_report.json').as_posix()}")
        print(f"Catalog 覆盖报告：{(args.output / '_catalog_coverage_report.json').as_posix()}")
        if args.collect_output is not None:
            return collect_and_report(args.output, args.collect_output, args.overwrite)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
