"""strings.json, en.json and de.json have the same keys; every key used in code exists."""

from __future__ import annotations

import json
import pathlib
import re

COMP = pathlib.Path(__file__).parent.parent / "custom_components" / "ps5_hass"


def _keys(d: dict, prefix: str = "") -> set[str]:
    out: set[str] = set()
    for k, v in d.items():
        path = f"{prefix}.{k}" if prefix else k
        out |= _keys(v, path) if isinstance(v, dict) else {path}
    return out


def test_translations_complete() -> None:
    strings = _keys(json.loads((COMP / "strings.json").read_text()))
    for lang in ("en", "de"):
        other = _keys(json.loads((COMP / "translations" / f"{lang}.json").read_text()))
        assert strings == other, (lang, strings ^ other)


def test_used_keys_exist() -> None:
    strings = json.loads((COMP / "strings.json").read_text())
    code = "\n".join(p.read_text() for p in COMP.glob("*.py"))
    for key in set(re.findall(r'translation_key="([a-z_]+)"', code)):
        assert key in strings["exceptions"] or key in strings["selector"] or any(
            key in platform for platform in strings["entity"].values()
        ), key
    for key in set(re.findall(r'errors?\[?"base"\]? ?[:=] ?"([a-z_]+)"', code)) | set(
        re.findall(r'\{"base": "([a-z_]+)"\}', code)
    ):
        assert key in strings["config"]["error"] or key in strings["options"]["error"], key
    for key in set(re.findall(r'reason="([a-z_]+)"', code)) | {"already_configured", "reauth_successful"}:
        assert key in strings["config"]["abort"] or key in strings["options"]["abort"], key
