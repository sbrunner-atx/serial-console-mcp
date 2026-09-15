"""The skill files exist, have frontmatter, and mention what they promise."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

SKILLS = Path(__file__).resolve().parents[1] / "skills"
EXPECT = {
    "serial-console": ["read_until_prompt", "send_keys", "screen", "presets", "ctrl-c",
                       "alarm and control panels", "IoT", "detect_baud"],
    "junos-operating": ["cli", "configure exclusive", "show | compare", "commit confirmed",
                        "rollback 0", "rollback 1", "show interfaces terse", "show system alarms",
                        "set cli screen-length 0", "exit", "cli -c", "start shell",
                        "show chassis routing-engine", "show chassis environment",
                        "show system storage", "show interfaces brief", "show system status",
                        "---(more", "commit check", "configuration check succeeds",
                        "load complete", "Users currently editing"],
    "ios-operating": ["enable", "configure terminal", "end", "write memory", "reload in 10",
                      "reload cancel", "show ip interface brief", "show running-config",
                      "terminal length 0", "--More--", "show processes cpu sorted",
                      "show environment all", "show interfaces status", "show interfaces brief"],
}


@pytest.mark.parametrize("name", sorted(EXPECT))
def test_skill_file(name):
    text = (SKILLS / name / "SKILL.md").read_text()
    m = re.match(r"---\nname: (\S+)\ndescription: >\n(?:  .*\n)+---\n", text)
    assert m and m.group(1) == name, "frontmatter must name the skill"
    for needle in EXPECT[name]:
        assert needle in text, f"{name} should mention {needle!r}"


def test_ios_skill_uses_no_junos_pipes():
    text = (SKILLS / "ios-operating" / "SKILL.md").read_text()
    table = [ln for ln in text.splitlines() if ln.startswith("|")]
    for line in table:
        assert "last 50" not in line and "no-more" not in line, line


def test_skill_tables_escape_pipes():
    """A literal | inside a table cell must be written \\| or the row breaks apart."""
    for name in EXPECT:
        text = (SKILLS / name / "SKILL.md").read_text()
        rows = [ln for ln in text.splitlines() if ln.startswith("| ") and "---" not in ln]
        for ln in rows:
            cols = len(re.split(r"(?<!\\)\|", ln)) - 2
            assert cols in (2, 3), f"{name}: {ln!r} splits into {cols} columns"
