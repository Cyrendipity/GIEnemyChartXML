#!/usr/bin/env python3
"""从 GIEnemyChart 的 FXG/MXML 源文件生成怪物 JSON 片段。"""

from __future__ import annotations

import argparse
import difflib
import html
import json
import re
import sys
import urllib.parse
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple


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
            and label.x - 120 <= block.x <= label.x + 5
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


def embedded_basename(source: str) -> Optional[str]:
    match = re.search(r"@Embed\(['\"](.+?)['\"]\)", source)
    raw = match.group(1) if match else source
    if not raw:
        return None
    return Path(urllib.parse.unquote(raw)).name


def relative_display_path(path: Path, repo_root: Path) -> str:
    try:
        return path.resolve().relative_to(repo_root.resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def resolve_asset(source_path: Path, image: ImageBlock, repo_root: Path) -> Optional[str]:
    basename = embedded_basename(image.source)
    if not basename:
        return None
    asset_dir = source_path.with_suffix(".assets")
    if not asset_dir.is_dir():
        return None
    lower_name = basename.lower()
    actual = next((path for path in asset_dir.iterdir() if path.name.lower() == lower_name), None)
    return relative_display_path(actual, repo_root) if actual else None


def skill_icon(
    fxg: Optional[SourceDocument],
    header: TextBlock,
    repo_root: Path,
) -> Optional[str]:
    if fxg is None:
        return None
    target_x = header.x - 140
    target_y = header.y + 11
    candidates = []
    for image in fxg.images:
        distance = abs(image.x - target_x) + abs(image.y - target_y)
        if distance <= 48:
            candidates.append((distance, image))
    if not candidates:
        return None
    image = min(candidates, key=lambda item: item[0])[1]
    return resolve_asset(fxg.path, image, repo_root)


def extract_skills(
    fxg: Optional[SourceDocument],
    mxml: Optional[SourceDocument],
    repo_root: Path,
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
        skill = {"Name": name, "Type": skill_type}
        icon = skill_icon(fxg, header, repo_root)
        if icon:
            skill["IconPath"] = icon
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
        shield = {"Name": name, "Description": description}
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
        chargebar = {"Name": name, "Description": description}
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


def generate_fragment(source: MonsterSource, repo_root: Path) -> Tuple[list, dict]:
    warnings: List[str] = []
    fxg = parse_document(source.fxg, parse_fxg, warnings)
    mxml = parse_document(source.mxml, parse_mxml, warnings)
    if source.fxg is None:
        warnings.append("没有配对的 FXG 文件")
    if source.mxml is None:
        warnings.append("没有配对的 MXML 文件")
    resistance = extract_resistance(fxg, warnings)
    skills, used_positions = extract_skills(fxg, mxml, repo_root, warnings)
    notes = extract_notes(fxg, mxml, used_positions)
    shields = extract_shields(notes, skills)
    chargebars = extract_chargebars(notes)
    fields = {
        "Resistace": resistance,
        "Skill": skills,
        "Notes": notes,
        "Shield": shields,
        "Chargebar": chargebars,
    }
    payload = [{source.name: fields}]
    report = {
        "name": source.name,
        "category": source.category,
        "fxg": source.fxg.as_posix() if source.fxg else None,
        "mxml": source.mxml.as_posix() if source.mxml else None,
        "counts": {
            "resistance_states": len(resistance),
            "skills": len(skills),
            "notes": len(notes),
            "shields": len(shields),
            "chargebars": len(chargebars),
        },
        "warnings": warnings,
    }
    return payload, report


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="遍历 FXG/MXML，为每个怪物生成抗性、技能、机注、护盾和计量条 JSON 片段。"
    )
    parser.add_argument("--fxg-root", type=Path, default=Path("fxg"), help="FXG 根目录")
    parser.add_argument("--mxml-root", type=Path, default=Path("mxml"), help="MXML 根目录")
    parser.add_argument("--output", type=Path, default=Path("generated_json"), help="输出目录")
    parser.add_argument("--include", help="只处理名称或分类中包含此文本的怪物")
    parser.add_argument("--limit", type=int, help="最多处理多少个怪物，便于抽样检查")
    parser.add_argument("--overwrite", action="store_true", help="覆盖已经生成的 JSON")
    parser.add_argument("--dry-run", action="store_true", help="解析但不写入文件")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    repo_root = Path.cwd()
    sources = discover_sources(args.fxg_root, args.mxml_root)
    if args.include:
        sources = [source for source in sources if args.include in source.name or args.include in source.category]
    if args.limit is not None:
        sources = sources[: max(0, args.limit)]
    if not sources:
        print("没有找到符合条件的 FXG/MXML 文件。", file=sys.stderr)
        return 1
    reports = []
    written = 0
    skipped = 0
    for source in sources:
        payload, report = generate_fragment(source, repo_root)
        output_path = args.output / source.category / f"{source.name}.json"
        report["output"] = output_path.as_posix()
        reports.append(report)
        if args.dry_run:
            continue
        if output_path.exists() and not args.overwrite:
            skipped += 1
            continue
        write_json(output_path, payload)
        written += 1
    if not args.dry_run:
        write_json(args.output / "_generation_report.json", reports)
    warning_count = sum(len(report["warnings"]) for report in reports)
    print(
        f"处理 {len(reports)} 个怪物：写入 {written}，跳过 {skipped}，报告警告 {warning_count}。"
    )
    if not args.dry_run:
        print(f"输出目录：{args.output.as_posix()}")
        print(f"检查报告：{(args.output / '_generation_report.json').as_posix()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
