"""Convert legacy Bk2 datapoint definitions into a BkNG ``global.var`` file.

The program has no third-party dependencies.  Run it with::

    python bkng_converter.py

Use the two folder buttons to select the old and new Automation Studio project
folders, then click *Convert*. The selected new ``global.var`` is replaced.
Optionally, all tasks containing ladder diagrams (``.ld``) are copied from the
old into the new project, keeping their folder structure.
"""

from __future__ import annotations

import re
import shutil
import sys
import tkinter as tk
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext, ttk
from typing import Callable, Iterable

ICON_PATH = Path(getattr(sys, "_MEIPASS", Path(__file__).parent)) / "assets" / "icon.ico"


REQUIRED_OLD_FILES = ("dplist.dat", "gruppen.dat", "global.var")
REQUIRED_VIS_UST_STATUS = "VisUstStat : ARRAY[0..7] OF BOOL;"
VARIABLE_RE = re.compile(
    r"^\s*(?P<name>[A-Za-z_][\w]*)\s*:\s*(?P<type>[A-Za-z_][\w.]*)\s*;"
    r"\s*(?P<comment>\(\*.*?\*\))?\s*$"
)
GENERIC_VARIABLE_RE = re.compile(r"^\s*(?P<name>[A-Za-z_][\w]*)\s*:\s*.+;\s*(?:\(\*.*?\*\)\s*)?$")
# A VAR section header, e.g. "VAR", "VAR_GLOBAL", "VAR CONSTANT" or "VAR RETAIN".
# Anchored start-to-end so it never matches an ordinary "name : type;" declaration.
VAR_SECTION_RE = re.compile(r"^\s*VAR(?:_[A-Za-z]+)?((?:\s+[A-Za-z]+)*)\s*$", re.IGNORECASE)
END_VAR_RE = re.compile(r"^\s*END_VAR\s*$", re.IGNORECASE)
# IEC 61131-3 qualifiers that change a section's semantics and must be preserved.
SECTION_QUALIFIER_KEYWORDS = ("CONSTANT", "RETAIN", "PERSISTENT")
DATAPOINT_RE = re.compile(r'^\s*"?\s*@(?P<name>[A-Za-z_][\w]*)\b')
GROUP_RE = re.compile(r'^\s*"?\s*\$(?P<name>[^"\r\n]+)')
BLOCK_RE = re.compile(r'^\s*"?\s*\$(?P<name>[A-Za-z][A-Za-z0-9_]*)\b')
TEXT_RE = re.compile(r'^\s*"?\s*~(?P<text>.*?)(?:"\s*,?\s*)?$')
ASSIGNMENT_RE = re.compile(
    r"(?P<key>[A-Za-z_][\w.]*)\s*=\s*"
    r"(?P<value>'[^']*'|\"[^\"]*\"|[^\s,;]+)"
)


@dataclass
class Parameter:
    key: str
    value: str


@dataclass
class Datapoint:
    name: str
    text: str = ""
    pab_parameters: list[Parameter] = field(default_factory=list)
    vis_parameters: list[Parameter] = field(default_factory=list)
    block_defaults: dict[str, list[Parameter]] = field(default_factory=dict)


@dataclass
class ConversionResult:
    target: Path
    converted: int
    copied: int
    unmatched: int
    warnings: list[str]


@dataclass
class LadderCopyResult:
    tasks: list[Path]
    files_copied: int
    files_overwritten: int
    packages_updated: list[Path]


class ConversionError(RuntimeError):
    """Raised when project files cannot be discovered or converted safely."""


# Called as chooser(filename, root, candidates) when a file is ambiguous; returns
# the selected path, or None if the user cancelled.
FileChooser = Callable[[str, Path, list[Path]], "Path | None"]


LANGUAGES = {"en": "English", "de": "Deutsch"}
TEXTS: dict[str, dict[str, str]] = {
    "en": {
        "not_found": "Could not find '{filename}' below:\n{root}",
        "choice_cancelled": "No '{filename}' was selected; conversion cancelled.",
        "ambiguous": "More than one '{filename}' was found below {root}.\n"
                     "Please select a folder that contains exactly one copy:\n{choices}",
        "no_directories": "Both selections must be existing project directories.",
        "warn_no_group": "{name}: no group found; Gruppe was omitted.",
        "warn_unmatched": "{count} datapoint(s) in dplist.dat have no declaration in the old global.var.",
        "no_declarations": "No variable declarations were found in the old global.var.",
        "no_logical": "No 'Logical' folder was found in:\n{root}",
        "no_objects_section": "Cannot add '{child}' to {package}: no <Objects> section found.",
        "no_ladder_tasks": "No tasks with .ld files were found below:\n{root}",
        "choice_title": "Select {filename}",
        "choice_prompt": "More than one '{filename}' was found below:\n{root}\n\nPlease select the file to use:",
        "ok": "OK",
        "cancel": "Cancel",
        "language": "Language",
        "old_folder": "Old project folder",
        "new_folder": "New project folder",
        "browse": "Browse…",
        "copy_ladder": "Also copy all ladder diagram (.ld) tasks into the new project",
        "convert": "Convert",
        "start_hint": "Select the old and new project folders, then click Convert.\n",
        "select_old": "Select old project directory",
        "select_new": "Select new project directory",
        "using_file": "Using {filename}: {path}\n",
        "error": "ERROR: {error}\n",
        "conversion_failed": "Conversion failed",
        "summary": "Converted {converted} datapoint declaration(s); copied {copied} unchanged.\nWritten: {target}\n",
        "warnings": "Warnings:",
        "ladder_error": "ERROR while copying ladder tasks: {error}\n",
        "ladder_failed": "Copying ladder tasks failed",
        "ladder_tasks": "Copied ladder tasks:",
        "ladder_summary": "Copied {tasks} ladder task(s) with {files} file(s) ({overwritten} overwritten); "
                          "updated {packages} Package.pkg file(s).\n",
        "conversion_complete": "Conversion complete",
    },
    "de": {
        "not_found": "'{filename}' wurde nicht gefunden unter:\n{root}",
        "choice_cancelled": "Keine '{filename}' ausgewählt; Konvertierung abgebrochen.",
        "ambiguous": "Mehr als eine '{filename}' wurde unter {root} gefunden.\n"
                     "Bitte einen Ordner wählen, der genau eine Datei enthält:\n{choices}",
        "no_directories": "Beide Auswahlen müssen existierende Projektverzeichnisse sein.",
        "warn_no_group": "{name}: keine Gruppe gefunden; Gruppe wurde weggelassen.",
        "warn_unmatched": "{count} Datenpunkt(e) in dplist.dat haben keine Deklaration in der alten global.var.",
        "no_declarations": "In der alten global.var wurden keine Variablendeklarationen gefunden.",
        "no_logical": "Kein Ordner 'Logical' gefunden in:\n{root}",
        "no_objects_section": "'{child}' kann nicht in {package} eingetragen werden: kein <Objects>-Abschnitt gefunden.",
        "no_ladder_tasks": "Keine Tasks mit .ld-Dateien gefunden unter:\n{root}",
        "choice_title": "{filename} auswählen",
        "choice_prompt": "Mehr als eine '{filename}' wurde gefunden unter:\n{root}\n\n"
                         "Bitte die zu verwendende Datei auswählen:",
        "ok": "OK",
        "cancel": "Abbrechen",
        "language": "Sprache",
        "old_folder": "Altes Projektverzeichnis",
        "new_folder": "Neues Projektverzeichnis",
        "browse": "Durchsuchen…",
        "copy_ladder": "Zusätzlich alle Kontaktplan-Tasks (.ld) in das neue Projekt kopieren",
        "convert": "Konvertieren",
        "start_hint": "Altes und neues Projektverzeichnis auswählen, dann auf Konvertieren klicken.\n",
        "select_old": "Altes Projektverzeichnis auswählen",
        "select_new": "Neues Projektverzeichnis auswählen",
        "using_file": "Verwende {filename}: {path}\n",
        "error": "FEHLER: {error}\n",
        "conversion_failed": "Konvertierung fehlgeschlagen",
        "summary": "{converted} Datenpunkt-Deklaration(en) konvertiert; {copied} unverändert übernommen.\n"
                   "Geschrieben: {target}\n",
        "warnings": "Warnungen:",
        "ladder_error": "FEHLER beim Kopieren der Kontaktplan-Tasks: {error}\n",
        "ladder_failed": "Kopieren der Kontaktplan-Tasks fehlgeschlagen",
        "ladder_tasks": "Kopierte Kontaktplan-Tasks:",
        "ladder_summary": "{tasks} Kontaktplan-Task(s) mit {files} Datei(en) kopiert ({overwritten} überschrieben); "
                          "{packages} Package.pkg-Datei(en) aktualisiert.\n",
        "conversion_complete": "Konvertierung abgeschlossen",
    },
}
_language = "de"


def set_language(language: str) -> None:
    global _language
    if language not in TEXTS:
        raise ValueError(f"Unsupported language: {language}")
    _language = language


def tr(key: str, **values: object) -> str:
    """Return the text for ``key`` in the current language."""
    return TEXTS[_language][key].format(**values)


def read_text_with_encoding(path: Path) -> tuple[str, str]:
    """Read legacy files while retaining German characters where possible."""
    raw = path.read_bytes()
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw.decode("utf-8-sig"), "utf-8-sig"
    for encoding in ("utf-8", "cp1252", "latin-1"):
        try:
            return raw.decode(encoding), encoding
        except UnicodeDecodeError:
            pass
    return raw.decode("latin-1", errors="replace"), "latin-1"


def read_text(path: Path) -> str:
    return read_text_with_encoding(path)[0]


def discover_file(root: Path, filename: str, chooser: FileChooser | None = None) -> Path:
    matches = sorted(
        (path for path in root.rglob("*") if path.is_file() and path.name.casefold() == filename.casefold()),
        key=lambda path: (len(path.relative_to(root).parts), str(path).casefold()),
    )
    if not matches:
        raise ConversionError(tr("not_found", filename=filename, root=root))
    # Automation Studio keeps generated copies below Temp.  Prefer the editable
    # source file below Logical when a project root is selected.
    logical_matches = [path for path in matches if any(part.casefold() == "logical" for part in path.relative_to(root).parts)]
    if len(logical_matches) == 1:
        return logical_matches[0]
    if len(matches) > 1 and chooser is not None:
        selected = chooser(filename, root, matches)
        if selected is None:
            raise ConversionError(tr("choice_cancelled", filename=filename))
        return selected
    if len(matches) > 1:
        choices = "\n".join(str(path.relative_to(root)) for path in matches)
        raise ConversionError(tr("ambiguous", filename=filename, root=root, choices=choices))
    return matches[0]


def strip_legacy_wrapping(line: str) -> str:
    """Remove the optional double quote/comma wrapper used in .dat modules."""
    text = line.strip()
    if text.startswith('"'):
        text = text[1:]
        closing = text.rfind('"')
        if closing >= 0:
            text = text[:closing] + text[closing + 1 :]
    return text.strip().rstrip(",").strip()


def parse_assignments(line: str) -> list[Parameter]:
    return [Parameter(match.group("key"), match.group("value")) for match in ASSIGNMENT_RE.finditer(line)]


def parse_block_defaults(dplist_text: str) -> dict[str, list[Parameter]]:
    """Return default parameter blocks keyed by legacy type suffix (RK, AP, ...)."""
    defaults: dict[str, list[Parameter]] = {}
    active: str | None = None
    for raw_line in dplist_text.splitlines():
        line = strip_legacy_wrapping(raw_line)
        if not line or line.startswith(";"):
            continue
        block = BLOCK_RE.match(line)
        if block:
            active = block.group("name").upper()
            # A new block replaces the preceding definition for that type.
            defaults[active] = []
            continue
        if DATAPOINT_RE.match(line):
            active = None
            continue
        if active and re.match(r"^#(?:PAB|VIS)\b", line, re.IGNORECASE):
            defaults[active].extend(parse_assignments(line))
    return defaults


def parse_datapoints(dplist_text: str) -> dict[str, Datapoint]:
    """Parse datapoints and the latest preceding defaults for each one."""
    datapoints: dict[str, Datapoint] = {}
    active: Datapoint | None = None
    active_block: str | None = None
    current_defaults: dict[str, list[Parameter]] = {}
    for raw_line in dplist_text.splitlines():
        line = strip_legacy_wrapping(raw_line)
        if not line or line.startswith(";"):
            continue
        block = BLOCK_RE.match(line)
        if block:
            active = None
            active_block = block.group("name").upper()
            # The last block definition is authoritative from this point on.
            current_defaults[active_block] = []
            continue
        match = DATAPOINT_RE.match(line)
        if match:
            active = Datapoint(
                match.group("name"),
                block_defaults={key: list(value) for key, value in current_defaults.items()},
            )
            datapoints[active.name.casefold()] = active
            active_block = None
            continue
        if active_block and re.match(r"^#(?:PAB|VIS)\b", line, re.IGNORECASE):
            current_defaults[active_block].extend(parse_assignments(line))
            continue
        if active is None:
            continue
        text = TEXT_RE.match(line)
        if text:
            active.text = text.group("text").strip().strip('"').strip()
        elif re.match(r"^#PAB\b", line, re.IGNORECASE):
            active.pab_parameters.extend(parse_assignments(line))
        elif re.match(r"^#VIS\b", line, re.IGNORECASE):
            active.vis_parameters.extend(parse_assignments(line))
        # #VI1 and #UVIS, along with unrelated module settings, are ignored.
    return datapoints


def parse_groups(groups_text: str) -> dict[str, str]:
    groups: dict[str, str] = {}
    active_group: str | None = None
    for raw_line in groups_text.splitlines():
        line = strip_legacy_wrapping(raw_line)
        if not line or line.startswith(";"):
            continue
        group = GROUP_RE.match(line)
        if group:
            # The complete text after "$" is the user-visible group name.
            active_group = group.group("name").strip()
            continue
        datapoint = DATAPOINT_RE.match(line)
        if datapoint and active_group:
            groups[datapoint.group("name").casefold()] = active_group
    return groups


def type_block_name(var_type: str) -> str:
    return re.sub(r"^Bk", "", var_type, flags=re.IGNORECASE).upper()


def mapped_key(key: str, var_type: str) -> str | None:
    key_lower = key.casefold()
    if key_lower == "y.einh":
        return "UnitY"
    if key_lower.endswith(".einh"):
        return "Unit"
    # These legacy fields are used by the old runtime but are not fields of
    # the corresponding BkNG structures (confirmed by the BkNG example).
    if var_type.casefold() == "bkrk" and key_lower in {"w", "xw"}:
        return None
    if var_type.casefold() == "bksw" and key_lower == "sw":
        return None
    if key_lower.endswith(".kpos"):
        prefix = key_lower[:-5]
        if var_type.casefold() == "bkrk":
            if prefix == "w":
                return "KposXW"
            if prefix == "y":
                return "KposY"
        return "Kpos"
    replacements = {
        "w.max": "Wmax", "w.min": "Wmin", "sw.max": "SWmax", "sw.min": "SWmin",
        "yrmin": "YrMin", "yrmax": "YrMax", "smax": "Smax", "wert.t0": "BP0_Name",
        "wert.t1": "BP1_Name", "xwg": "Xwg", "bamax": "BAmax", "swaut": "SWaut",
        "ba.t0": "BA0_Name", "ba.t1": "BA1_Name", "ba.t2": "BA2_Name", "ba.t3": "BA3_Name",
        "ba.t4": "BA4_Name", "saus.t0": "SB0_Name", "saus.t1": "SB1_Name",
        "saus.t2": "SB2_Name", "saus.t3": "SB3_Name", "saus.t4": "SB4_Name",
        "rmgsb.t0": "SB0_RmName", "rmgsb.t1": "SB1_RmName", "rmgsb.t2": "SB2_RmName",
        "rmgsb.t3": "SB3_RmName", "rmg.t0": "SBX_RmName", "rmg.t1": "SB0_RmName",
        "rmg.t2": "SB1_RmName", "rmg.t3": "SBY_RmName",
    }
    return replacements.get(key_lower, key)


def format_value(value: str, key: str) -> str:
    value = value.strip()
    if key.casefold() == "hand" and value.casefold() == "a":
        return "0"
    if key.casefold() == "int" and value.casefold() in {"i", "e"}:
        return "1" if value.casefold() == "i" else "0"
    if value.startswith(("'", '"')) and value.endswith(("'", '"')):
        return "'" + value[1:-1].replace("'", "''") + "'"
    string_key = key in {"Unit", "UnitY"} or key.endswith(("_Name", "RmName"))
    if string_key and not re.fullmatch(r"[-+]?\d+(?:[.,]\d+)?", value):
        return "'" + value.replace("'", "''") + "'"
    return value


def add_parameter(parameters: OrderedDict[str, str], key: str, value: str, var_type: str) -> None:
    new_key = mapped_key(key, var_type)
    if new_key is None:
        return
    # A later explicit datapoint parameter replaces a default of the same key.
    parameters[new_key.casefold()] = f"{new_key}:={format_value(value, new_key)}"


def render_datapoint(declaration: re.Match[str], datapoint: Datapoint, group: str | None,
                     defaults: Iterable[Parameter]) -> str:
    name, var_type = declaration.group("name"), declaration.group("type")
    parameters: OrderedDict[str, str] = OrderedDict()
    # The order is deliberately readable: visual data/text, explicit PAB data, then defaults.
    for parameter in datapoint.vis_parameters:
        add_parameter(parameters, parameter.key, parameter.value, var_type)
    if datapoint.text:
        parameters["klartext"] = "Klartext:='" + datapoint.text.replace("'", "''") + "'"
    for parameter in datapoint.pab_parameters:
        add_parameter(parameters, parameter.key, parameter.value, var_type)
    for parameter in defaults:
        key = mapped_key(parameter.key, var_type)
        if key is None:
            continue
        parameters.setdefault(key.casefold(), f"{key}:={format_value(parameter.value, key)}")
    if group:
        parameters["gruppe"] = "Gruppe:='" + group.replace("'", "''") + "'"
    comment = datapoint.text or (declaration.group("comment") or "").removeprefix("(*").removesuffix("*)").strip()
    suffix = f" (*{comment}*)" if comment else ""
    if not parameters:
        return f"{name} : {var_type};{suffix}"
    return f"{name} : {var_type} := ({','.join(parameters.values())});{suffix}"


def normalize_var_header(section: re.Match[str]) -> str:
    """Rebuild a VAR section header, keeping only qualifiers that matter (CONSTANT/RETAIN/...)."""
    qualifiers = [word.upper() for word in section.group(1).split() if word.upper() in SECTION_QUALIFIER_KEYWORDS]
    return " ".join(["VAR", *qualifiers])


def convert(old_root: Path, new_root: Path, chooser: FileChooser | None = None) -> ConversionResult:
    old_root, new_root = old_root.resolve(), new_root.resolve()
    if not old_root.is_dir() or not new_root.is_dir():
        raise ConversionError(tr("no_directories"))
    old_files = {name: discover_file(old_root, name, chooser) for name in REQUIRED_OLD_FILES}
    target = discover_file(new_root, "global.var", chooser)

    old_global = read_text(old_files["global.var"])
    target_text, target_encoding = read_text_with_encoding(target)
    dplist = read_text(old_files["dplist.dat"])
    datapoints = parse_datapoints(dplist)
    groups = parse_groups(read_text(old_files["gruppen.dat"]))

    output: list[str] = []
    converted = copied = unmatched = 0
    warnings: list[str] = []
    visuststat_inserted = False
    for line in old_global.splitlines():
        section = VAR_SECTION_RE.match(line)
        if section:
            header = normalize_var_header(section)
            output.append(header)
            # The mandatory declaration must live in a plain (unqualified) section;
            # it is inserted once, into the first such section encountered.
            if header == "VAR" and not visuststat_inserted:
                output.append(REQUIRED_VIS_UST_STATUS)
                visuststat_inserted = True
            continue
        if END_VAR_RE.match(line):
            output.append("END_VAR")
            continue
        generic_declaration = GENERIC_VARIABLE_RE.match(line)
        if not generic_declaration:
            continue
        if generic_declaration.group("name").casefold() == "visuststat":
            # This mandatory declaration is emitted once, above, instead.
            continue
        declaration = VARIABLE_RE.match(line)
        if declaration is None:
            output.append(line)
            copied += 1
            continue
        name = declaration.group("name")
        datapoint = datapoints.get(name.casefold())
        if datapoint is None:
            output.append(line)
            copied += 1
            continue
        is_status = declaration.group("type").casefold() == "bkstat"
        # BkStat datapoints never carry a Gruppe, even if gruppen.dat lists them.
        group = None if is_status else groups.get(name.casefold())
        if group is None and not is_status and name.casefold() != "uststatus":
            warnings.append(tr("warn_no_group", name=name))
        output.append(render_datapoint(declaration, datapoint, group,
                                      datapoint.block_defaults.get(type_block_name(declaration.group("type")), [])))
        converted += 1

    declared_names = {
        match.group("name").casefold()
        for match in map(GENERIC_VARIABLE_RE.match, old_global.splitlines()) if match
    }
    unmatched = len(set(datapoints) - declared_names)
    if unmatched:
        warnings.append(tr("warn_unmatched", count=unmatched))
    if not output:
        raise ConversionError(tr("no_declarations"))
    if not visuststat_inserted:
        # The old file had no plain VAR section at all (unusual); add one up front.
        output = ["VAR", REQUIRED_VIS_UST_STATUS, "END_VAR", *output]

    newline = "\r\n" if "\r\n" in target_text else "\n"
    target.write_text(
        newline.join(output) + newline,
        encoding=target_encoding,
        newline="",
    )
    return ConversionResult(target, converted, copied, unmatched, warnings)


PACKAGE_FILE = "Package.pkg"
NEW_PACKAGE_TEMPLATE = (
    '﻿<?xml version="1.0" encoding="utf-8"?>\r\n'
    '<?AutomationStudio FileVersion="4.9"?>\r\n'
    '<Package xmlns="http://br-automation.co.at/AS/Package">\r\n'
    "  <Objects>\r\n"
    "  </Objects>\r\n"
    "</Package>"
)


def logical_dir(root: Path) -> Path:
    """Return the Logical folder of an Automation Studio project (or the folder itself)."""
    if root.name.casefold() == "logical":
        return root
    for child in root.iterdir():
        if child.is_dir() and child.name.casefold() == "logical":
            return child
    raise ConversionError(tr("no_logical", root=root))


def find_ladder_tasks(logical: Path) -> list[Path]:
    """Return the task folders below ``logical`` that contain .ld files, outermost first."""
    folders = sorted({path.parent for path in logical.rglob("*") if path.is_file() and path.suffix.casefold() == ".ld"},
                     key=lambda path: (len(path.parts), str(path).casefold()))
    tasks: list[Path] = []
    for folder in folders:
        # A nested folder is copied together with its enclosing task.
        if folder != logical and not any(task in folder.parents for task in tasks):
            tasks.append(folder)
    return tasks


def package_object_line(package_text: str, child: str) -> str | None:
    """Return the ``<Object ...>child</Object>`` entry of a Package.pkg, if listed."""
    match = re.search(rf"<Object\b[^>]*>\s*{re.escape(child)}\s*</Object>", package_text, re.IGNORECASE)
    return match.group(0) if match else None


def ensure_package_entry(old_parent: Path, new_parent: Path, child: str, is_task: bool) -> bool:
    """List ``child`` in the new parent's Package.pkg, reusing the old entry. Returns True if changed."""
    new_package = new_parent / PACKAGE_FILE
    if new_package.exists():
        text, encoding = read_text_with_encoding(new_package)
    else:
        text, encoding = NEW_PACKAGE_TEMPLATE, "utf-8"
    if package_object_line(text, child):
        return False
    old_package = old_parent / PACKAGE_FILE
    entry = package_object_line(read_text(old_package), child) if old_package.exists() else None
    if entry is None:
        entry = (f'<Object Type="Program" Language="IEC">{child}</Object>' if is_task
                 else f'<Object Type="Package">{child}</Object>')
    closing = re.search(r"^([ \t]*)</Objects>", text, re.IGNORECASE | re.MULTILINE)
    if closing is None:
        raise ConversionError(tr("no_objects_section", child=child, package=new_package))
    newline = "\r\n" if "\r\n" in text else "\n"
    indent = closing.group(1) + "  "
    text = text[:closing.start()] + indent + entry + newline + text[closing.start():]
    new_package.write_text(text, encoding=encoding, newline="")
    return True


def copy_ladder_tasks(old_root: Path, new_root: Path) -> LadderCopyResult:
    """Copy every task containing .ld files into the new project, keeping the folder structure.

    Missing package folders are created and each parent Package.pkg is extended
    so that Automation Studio shows the copied packages and programs.
    """
    old_logical, new_logical = logical_dir(old_root.resolve()), logical_dir(new_root.resolve())
    tasks = find_ladder_tasks(old_logical)
    if not tasks:
        raise ConversionError(tr("no_ladder_tasks", root=old_logical))
    copied = overwritten = 0
    packages_updated: list[Path] = []
    for task in tasks:
        relative = task.relative_to(old_logical)
        for source in sorted(task.rglob("*")):
            if not source.is_file():
                continue
            destination = new_logical / relative / source.relative_to(task)
            destination.parent.mkdir(parents=True, exist_ok=True)
            overwritten += destination.exists()
            shutil.copy2(source, destination)
            copied += 1
        # Register the task and every package folder above it with its parent package.
        for depth in range(len(relative.parts)):
            parent_relative = Path(*relative.parts[:depth])
            child = relative.parts[depth]
            new_parent = new_logical / parent_relative
            if ensure_package_entry(old_logical / parent_relative, new_parent, child,
                                    is_task=depth == len(relative.parts) - 1):
                package = new_parent / PACKAGE_FILE
                if package not in packages_updated:
                    packages_updated.append(package)
    return LadderCopyResult([task.relative_to(old_logical) for task in tasks], copied, overwritten, packages_updated)


class FileChoiceDialog(tk.Toplevel):
    """Modal dialog that lets the user pick one of several files by full path."""

    def __init__(self, master: tk.Misc, filename: str, root: Path, candidates: list[Path]) -> None:
        super().__init__(master)
        self.candidates = candidates
        self.result: Path | None = None
        self.title(tr("choice_title", filename=filename))
        self.transient(master.winfo_toplevel())
        self.minsize(500, 250)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        ttk.Label(
            self, padding=(10, 10, 10, 6), justify="left",
            text=tr("choice_prompt", filename=filename, root=root),
        ).grid(row=0, column=0, columnspan=2, sticky="w")

        width = min(max(len(str(path)) for path in candidates) + 2, 160)
        self.listbox = tk.Listbox(self, width=width, height=min(len(candidates), 12),
                                  activestyle="dotbox", exportselection=False)
        yscroll = ttk.Scrollbar(self, orient="vertical", command=self.listbox.yview)
        xscroll = ttk.Scrollbar(self, orient="horizontal", command=self.listbox.xview)
        self.listbox.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)
        for path in candidates:
            self.listbox.insert(tk.END, str(path))
        self.listbox.selection_set(0)
        self.listbox.activate(0)
        self.listbox.grid(row=1, column=0, sticky="nsew", padx=(10, 0))
        yscroll.grid(row=1, column=1, sticky="ns", padx=(0, 10))
        xscroll.grid(row=2, column=0, sticky="ew", padx=(10, 0))

        buttons = ttk.Frame(self, padding=10)
        buttons.grid(row=3, column=0, columnspan=2, sticky="e")
        ttk.Button(buttons, text=tr("ok"), command=self._ok).pack(side="left", padx=(0, 8))
        ttk.Button(buttons, text=tr("cancel"), command=self.destroy).pack(side="left")

        self.listbox.bind("<Double-Button-1>", lambda _event: self._ok())
        self.bind("<Return>", lambda _event: self._ok())
        self.bind("<Escape>", lambda _event: self.destroy())
        self.protocol("WM_DELETE_WINDOW", self.destroy)

        self.listbox.focus_set()
        self.grab_set()
        self.wait_window()

    def _ok(self) -> None:
        selection = self.listbox.curselection()
        if selection:
            self.result = self.candidates[selection[0]]
            self.destroy()


class ConverterApp(ttk.Frame):
    def __init__(self, master: tk.Tk) -> None:
        super().__init__(master, padding=14)
        self.old_path = tk.StringVar()
        self.new_path = tk.StringVar()
        self.copy_ladder = tk.BooleanVar(value=False)
        self.language = tk.StringVar(value=LANGUAGES[_language])
        # Widgets whose text follows the selected language, with their text keys.
        self.translated: list[tuple[tk.Widget, str, str]] = []
        master.title("BkNG Converter V4")
        if ICON_PATH.exists():
            master.iconbitmap(default=str(ICON_PATH))
        master.minsize(700, 400)
        self.grid(sticky="nsew")
        master.columnconfigure(0, weight=1)
        master.rowconfigure(0, weight=1)
        self.columnconfigure(1, weight=1)
        self.rowconfigure(4, weight=1)

        language_row = ttk.Frame(self)
        language_row.grid(row=0, column=0, columnspan=3, pady=(0, 6), sticky="e")
        self._translate(ttk.Label(language_row), "language", suffix=":").pack(side="left", padx=(0, 6))
        language_box = ttk.Combobox(language_row, textvariable=self.language, values=list(LANGUAGES.values()),
                                    state="readonly", width=10)
        language_box.pack(side="left")
        language_box.bind("<<ComboboxSelected>>", lambda _event: self.change_language())

        self._path_row(1, "old_folder", self.old_path, self.choose_old)
        self._path_row(2, "new_folder", self.new_path, self.choose_new)
        self._translate(ttk.Checkbutton(self, variable=self.copy_ladder), "copy_ladder").grid(
            row=3, column=0, columnspan=2, pady=(12, 10), sticky="w")
        self._translate(ttk.Button(self, command=self.run_conversion), "convert").grid(
            row=3, column=2, pady=(12, 10), sticky="e")
        self.log = scrolledtext.ScrolledText(self, height=15, wrap=tk.WORD, state="disabled")
        self.log.grid(row=4, column=0, columnspan=3, sticky="nsew")
        self.write_log(tr("start_hint"))

    def _translate(self, widget: tk.Widget, key: str, suffix: str = "") -> tk.Widget:
        widget.configure(text=tr(key) + suffix)
        self.translated.append((widget, key, suffix))
        return widget

    def change_language(self) -> None:
        code = next(code for code, name in LANGUAGES.items() if name == self.language.get())
        set_language(code)
        for widget, key, suffix in self.translated:
            widget.configure(text=tr(key) + suffix)

    def _path_row(self, row: int, label: str, variable: tk.StringVar, command: object) -> None:
        self._translate(ttk.Label(self), label, suffix=":").grid(row=row, column=0, padx=(0, 8), pady=5, sticky="w")
        ttk.Entry(self, textvariable=variable).grid(row=row, column=1, pady=5, sticky="ew")
        self._translate(ttk.Button(self, command=command), "browse").grid(row=row, column=2, padx=(8, 0), pady=5)

    def choose_old(self) -> None:
        selection = filedialog.askdirectory(title=tr("select_old"))
        if selection:
            self.old_path.set(selection)

    def choose_new(self) -> None:
        selection = filedialog.askdirectory(title=tr("select_new"))
        if selection:
            self.new_path.set(selection)

    def write_log(self, text: str) -> None:
        self.log.configure(state="normal")
        self.log.insert(tk.END, text)
        self.log.see(tk.END)
        self.log.configure(state="disabled")

    def choose_file(self, filename: str, root: Path, candidates: list[Path]) -> Path | None:
        selected = FileChoiceDialog(self, filename, root, candidates).result
        if selected is not None:
            self.write_log(tr("using_file", filename=filename, path=selected))
        return selected

    def run_conversion(self) -> None:
        old_root, new_root = Path(self.old_path.get()), Path(self.new_path.get())
        try:
            result = convert(old_root, new_root, self.choose_file)
        except (ConversionError, OSError) as error:
            self.write_log(tr("error", error=error))
            messagebox.showerror(tr("conversion_failed"), str(error))
            return
        summary = tr("summary", converted=result.converted, copied=result.copied, target=result.target)
        if result.warnings:
            summary += tr("warnings") + "\n- " + "\n- ".join(result.warnings) + "\n"
        if self.copy_ladder.get():
            try:
                ladder = copy_ladder_tasks(old_root, new_root)
            except (ConversionError, OSError) as error:
                self.write_log(summary + tr("ladder_error", error=error))
                messagebox.showerror(tr("ladder_failed"), f"{summary}\n{error}")
                return
            self.write_log(tr("ladder_tasks") + "\n- " + "\n- ".join(str(task) for task in ladder.tasks) + "\n")
            summary += tr("ladder_summary", tasks=len(ladder.tasks), files=ladder.files_copied,
                          overwritten=ladder.files_overwritten, packages=len(ladder.packages_updated))
        self.write_log(summary)
        messagebox.showinfo(tr("conversion_complete"), summary)


def main() -> None:
    root = tk.Tk()
    ConverterApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
