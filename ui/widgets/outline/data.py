from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Optional, Literal

@dataclass
class ChapterVersion:
    name: str = "v1"
    lines: list[str] = field(default_factory=list)
    description: str = ""
    setting: str | None = None
    date: str | None = None
    characters: list[str] = field(default_factory=list)

@dataclass
class Chapter:
    title: str
    versions: list[ChapterVersion]
    id: int | None = None  # optional unique ID, e.g. from DB
    active_index: int = 0

    def __init__(self,
                 title: str,
                 id: int | None = None,
                 lines: list[str] | None = None,
                 description: str = "",
                 setting: str | None = None,
                 date: str | None = None,
                 characters: list[str] | None = None,
                 versions: list[ChapterVersion] | None = None,
                 active_index: int = 0,
                 editor_user_height: Optional[int] = None,
                 editor_height_mode: Literal["auto","user"]="auto"
                 ):
        self.title = title
        self.id = id
        if versions is not None:
            self.versions = versions
            self.active_index = max(0, min(active_index, len(versions)-1)) if versions else 0
            if not self.versions:
                self.versions = [ChapterVersion()]
        else: # if no versions, create one
            self.versions = [ChapterVersion(
                name="v1",
                lines=lines or [],
                description=description or "",
                setting=setting,
                date=date,
                characters=characters or [],
            )]
            self.active_index = 0

    def active(self) -> "ChapterVersion":
        if not self.versions:
            self.versions = [ChapterVersion()]
            self.active_index = 0
        return self.versions[self.active_index]

    # --- Back-compat properties (map to active version) ---
    @property
    def lines(self) -> list[str]:
        return self.active().lines
    @lines.setter
    def lines(self, v: list[str] | None):
        self.active().lines = list(v or [])

    @property
    def description(self) -> str:
        return self.active().description
    @description.setter
    def description(self, v: str):
        self.active().description = v or ""

    @property
    def setting(self) -> str | None:
        return self.active().setting
    @setting.setter
    def setting(self, v: str | None):
        self.active().setting = (v or None)

    @property
    def date(self) -> str | None:
        return self.active().date
    @date.setter
    def date(self, v: str | None):
        self.active().date = (v or None)

    @property
    def characters(self) -> list[str]:
        return self.active().characters
    @characters.setter
    def characters(self, names: list[str] | None):
        self.active().characters = [s for s in (names or []) if s]

def chapters_to_json(chapters) -> str:
    data = {"chapters": [
        {
            "title": ch.title,
            "active_index": ch.active_index,
            "versions": [{
                "name": v.name,
                "description": v.description,
                "setting": v.setting,
                "date": v.date,
                "characters": v.characters,
                "lines": v.lines,
            } for v in ch.versions],
        } for ch in chapters
    ]}
    return json.dumps(data, ensure_ascii=False, indent=2)

def chapters_from_json(s: str):
    raw = json.loads(s or "{}")
    out = []
    for r in (raw.get("chapters") or []):
        vers = r.get("versions")
        if not vers:
            # backward-compat (old shape): lines/description/setting in chapter
            v = ChapterVersion(
                name="v1",
                lines=r.get("lines") or [],
                description=r.get("description") or "",
                setting=r.get("setting"),
                date=r.get("date"),
                characters=r.get("characters") or [],
            )
            ch = Chapter(title=r.get("title") or "Untitled", versions=[v], active_index=0)
        else:
            ch = Chapter(title=r.get("title") or "Untitled",
                         versions=[ChapterVersion(**{
                             "name": v.get("name") or "v1",
                             "lines": v.get("lines") or [],
                             "description": v.get("description") or "",
                             "setting": v.get("setting"),
                             "date": v.get("date"),
                             "characters": v.get("characters") or [],
                         }) for v in vers],
                         active_index=min(max(0, r.get("active_index", 0)), len(vers)-1))
        out.append(ch)
    return out
