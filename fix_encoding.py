from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parent
MOJIBAKE_CHARS = "ЃЌЉЊЎЏЋѓќљњўџ"
TARGETS = [
    *sorted((ROOT / "app" / "templates").glob("*_v2.html")),
    ROOT / "app" / "templates" / "new_session_v3.html",
    ROOT / "app" / "templates" / "base_v2.html",
    ROOT / "app" / "static" / "js" / "editor_v2.js",
    ROOT / "app" / "static" / "js" / "session_runtime_v2.js",
    ROOT / "app" / "static" / "js" / "template_levels.js",
    ROOT / "app" / "static" / "js" / "map_editor_core.js",
    *sorted((ROOT / "app" / "domain").glob("*.py")),
    *sorted((ROOT / "app" / "api").glob("*.py")),
]


def normalize_to_utf8(path: Path) -> bool:
    data = path.read_bytes()
    if data.startswith(b"\xef\xbb\xbf"):
        path.write_text(data.decode("utf-8-sig"), encoding="utf-8", newline="\n")
        return True
    try:
        text = data.decode("utf-8")
        if any(ch in text for ch in MOJIBAKE_CHARS):
            try:
                repaired = text.encode("cp1251").decode("utf-8")
            except (UnicodeEncodeError, UnicodeDecodeError):
                return False
            if repaired != text:
                path.write_text(repaired, encoding="utf-8", newline="\n")
                return True
        return False
    except UnicodeDecodeError:
        try:
            text = data.decode("cp1251")
        except UnicodeDecodeError:
            return False
        path.write_text(text, encoding="utf-8", newline="\n")
        return True


def main() -> None:
    seen: set[Path] = set()
    for target in TARGETS:
        if not target.exists() or target in seen:
            continue
        seen.add(target)
        changed = normalize_to_utf8(target)
        status = "normalized" if changed else "skipped"
        print(f"{status}: {target.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
