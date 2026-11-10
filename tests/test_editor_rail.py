"""The tool rail: same groups in both apps, every tool once, icons present in both icon sets."""

from __future__ import annotations

import json
import re
from pathlib import Path

from umc.editor.rail import ICON_NAMES, MENUS, RAIL, as_json, items
from umc.editor.tools import TOOLS
from umc.gui.icon_data import ICONS

ROOT = Path(__file__).resolve().parents[1]


def test_the_website_has_the_same_rail():
    web = json.loads((ROOT / "web/src/editor/rail.json").read_text())
    assert web == json.loads(as_json())


def test_every_tool_is_on_the_rail_once_and_selection_comes_first():
    tools = [i for k, i, _l, _ic in items() if k == "tool"]
    assert sorted(tools) == sorted(cls.id for cls in TOOLS) and len(set(tools)) == len(tools)
    assert RAIL[0]["group"] == "Select" and tools[0] == "select"
    assert all(k in ("tool", "action", "menu") for k, *_ in items())
    assert set(MENUS) == {i for k, i, *_ in items() if k == "menu"}


def test_every_rail_icon_exists_in_both_icon_sets():
    ts = (ROOT / "web/src/ui/icon_data.ts").read_text()
    web = set(re.findall(r'^\s*"([a-z0-9-]+)":', ts, re.M))
    for name in ICON_NAMES:
        assert name in ICONS and name in web, name
