"""The skill files exist, have frontmatter, and mention what they promise."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

SKILLS = Path(__file__).resolve().parents[1] / "skills"
EXPECT = {
    "serial-console": ["read_until_prompt", "send_keys", "screen", "presets", "ctrl-c"],
    "junos-operating": ["cli", "configure exclusive", "show | compare", "commit confirmed",
                        "rollback 0", "rollback 1", "show interfaces terse", "show system alarms",
                        "set cli screen-length 0", "exit"],
    "ios-operating": ["enable", "configure terminal", "end", "write memory", "reload in 10",
                      "reload cancel", "show ip interface brief", "show running-config",
                      "terminal length 0", "--More--"],
}


@pytest.mark.parametrize("name", sorted(EXPECT))
def test_skill_file(name):
    text = (SKILLS / name / "SKILL.md").read_text()
    m = re.match(r"---\nname: (\S+)\ndescription: >\n(?:  .*\n)+---\n", text)
    assert m and m.group(1) == name, "frontmatter must name the skill"
    for needle in EXPECT[name]:
        assert needle in text, f"{name} should mention {needle!r}"
