"""Convert legacy Bk2 datapoint definitions into a BkNG ``global.var`` file.

The program has no third-party dependencies.  Run it with::

    python bkng_converter.py

Use the two folder buttons to select the old and new Automation Studio project
folders, then click *Convert*. The selected new ``global.var`` is replaced.
Optionally, all tasks containing ladder diagrams (``.ld``) are copied from the
old into the new project, keeping their folder structure, and the X20 I/O
modules (without the old PLC) including their I/O mapping are copied into the
active hardware configuration of the new project.
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
    ust_name: str | None = None
    ust_target: Path | None = None


@dataclass
class LadderCopyResult:
    tasks: list[Path]
    files_copied: int
    files_overwritten: int
    packages_updated: list[Path]
    deployed: list[tuple[str, str]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass
class IoCopyResult:
    hardware: Path
    io_map: Path
    modules: list[str]
    modules_replaced: int
    mappings_copied: int
    warnings: list[str]


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
        "no_physical": "No Automation Studio project (Physical/Physical.pkg) was found for:\n{root}",
        "no_configuration": "No hardware configuration was found in:\n{root}",
        "ambiguous_configuration": "The active configuration of the following project could not be determined:\n{root}",
        "no_cpu": "No PLC folder was found in the configuration:\n{root}",
        "cpu_not_in_hardware": "PLC '{cpu}' was not found in {path}.",
        "no_io_modules": "No X20 I/O modules connected to the PLC were found in {path}.",
        "io_name_is_cpu": "An I/O module of the old project has the name of the new PLC: '{name}'.",
        "invalid_hardware": "Invalid hardware file: {path}",
        "warn_io_target_skipped": "{module}: connection {connector} to '{target}' (part of the old PLC) was not copied.",
        "warn_connector_in_use": "{module} is also connected to {cpu}.{connector}; please check the topology.",
        "warn_mapping_skipped": "I/O mapping not copied (not an X20 I/O module): {line}",
        "copy_io": "Also copy X20 I/O modules incl. I/O mapping into the active configuration",
        "io_error": "ERROR while copying the I/O configuration: {error}\n",
        "io_failed": "Copying the I/O configuration failed",
        "io_summary": "Copied {modules} I/O module(s) ({replaced} replaced) and {mappings} I/O mapping(s) to:\n{hardware}\n",
        "warn_cpu_mapping": "CPU mapping taken over for {cpu}; please check that the channel exists on the new PLC: {line}",
        "warn_no_deployment": "Tasks were not added to a task class (Cpu.sw): {error}",
        "warn_task_not_deployed": "{task} is not assigned to a task class in the old project.",
        "warn_task_name_in_use": "A different task named '{task}' already exists in the new Cpu.sw; it was not added.",
        "warn_task_class_missing": "Task class '{task_class}' does not exist in the new Cpu.sw; '{task}' was not added.",
        "ladder_deployed": "Added {count} task(s) to their task class in Cpu.sw.\n",
        "ust_written": "UST name '{name}' written to UST_Name in:\n{path}\n",
        "warn_no_visu_variables": "UST name not written: no VisuTask/Variables.var found below {root}.",
        "warn_ust_name_not_written": "UST name '{name}' was not written: no Variables.var selected.",
        "warn_no_ust_name_declaration": "UST name not written: no UST_Name declaration found in {path}.",
        "warn_ust_name_too_long": "UST name '{name}' is longer than STRING[{length}] and will be truncated by the PLC.",
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
        "no_physical": "Kein Automation-Studio-Projekt (Physical/Physical.pkg) gefunden für:\n{root}",
        "no_configuration": "Keine Hardware-Konfiguration gefunden in:\n{root}",
        "ambiguous_configuration": "Die aktive Konfiguration dieses Projekts konnte nicht ermittelt werden:\n{root}",
        "no_cpu": "Kein SPS-Ordner in der Konfiguration gefunden:\n{root}",
        "cpu_not_in_hardware": "SPS '{cpu}' wurde in {path} nicht gefunden.",
        "no_io_modules": "In {path} wurden keine an der SPS angeschlossenen X20-I/O-Module gefunden.",
        "io_name_is_cpu": "Ein I/O-Modul des alten Projekts heißt wie die neue SPS: '{name}'.",
        "invalid_hardware": "Ungültige Hardware-Datei: {path}",
        "warn_io_target_skipped": "{module}: Verbindung {connector} zu '{target}' (gehört zur alten SPS) wurde nicht übernommen.",
        "warn_connector_in_use": "{module} ist ebenfalls an {cpu}.{connector} angeschlossen; bitte Topologie prüfen.",
        "warn_mapping_skipped": "I/O-Mapping nicht übernommen (kein X20-I/O-Modul): {line}",
        "copy_io": "Zusätzlich X20-I/O-Module inkl. I/O-Mapping in die aktive Konfiguration kopieren",
        "io_error": "FEHLER beim Kopieren der I/O-Konfiguration: {error}\n",
        "io_failed": "Kopieren der I/O-Konfiguration fehlgeschlagen",
        "io_summary": "{modules} I/O-Modul(e) ({replaced} ersetzt) und {mappings} I/O-Mapping(s) kopiert nach:\n{hardware}\n",
        "warn_cpu_mapping": "CPU-Mapping für {cpu} übernommen; bitte prüfen, ob der Kanal auf der neuen SPS existiert: {line}",
        "warn_no_deployment": "Tasks wurden keiner Taskklasse zugeordnet (Cpu.sw): {error}",
        "warn_task_not_deployed": "{task} ist im alten Projekt keiner Taskklasse zugeordnet.",
        "warn_task_name_in_use": "In der neuen Cpu.sw gibt es bereits einen anderen Task namens '{task}'; er wurde nicht eingetragen.",
        "warn_task_class_missing": "Taskklasse '{task_class}' existiert in der neuen Cpu.sw nicht; '{task}' wurde nicht eingetragen.",
        "ladder_deployed": "{count} Task(s) in ihre Taskklasse in der Cpu.sw eingetragen.\n",
        "ust_written": "UST-Name '{name}' auf UST_Name geschrieben in:\n{path}\n",
        "warn_no_visu_variables": "UST-Name nicht geschrieben: keine VisuTask/Variables.var unter {root} gefunden.",
        "warn_ust_name_not_written": "UST-Name '{name}' wurde nicht geschrieben: keine Variables.var ausgewählt.",
        "warn_no_ust_name_declaration": "UST-Name nicht geschrieben: keine Deklaration UST_Name in {path} gefunden.",
        "warn_ust_name_too_long": "UST-Name '{name}' ist länger als STRING[{length}] und wird von der SPS abgeschnitten.",
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
    ust_name = parse_ust_name(dplist)
    ust_target = write_ust_name(new_root, ust_name, chooser, warnings) if ust_name else None
    return ConversionResult(target, converted, copied, unmatched, warnings, ust_name, ust_target)


UST_NAME_RE = re.compile(r'^\s*"?\s*#VIS\s+(?:.*?\s)?UST\.Name\s*=\s*(?P<value>.*)$', re.IGNORECASE)
UST_NAME_DECLARATION_RE = re.compile(
    r"^(?P<head>\s*UST_Name\s*:\s*STRING(?:\s*\[\s*(?P<length>\d+)\s*\])?)\s*(?::=\s*'(?:\$.|[^'$])*'\s*)?;",
    re.IGNORECASE | re.MULTILINE)
VISU_TASK_FOLDER = "visutask"
VISU_VARIABLES_FILE = "variables.var"


def parse_ust_name(dplist_text: str) -> str | None:
    """Return the UST name from the ``#VIS UST.Name=...`` line of the dplist, if any.

    Commented lines and alternative languages (#VI1, #VIS1, ...) are ignored.
    """
    for raw_line in dplist_text.splitlines():
        match = UST_NAME_RE.match(raw_line)
        if not match:
            continue
        value = match.group("value").strip()
        if value.startswith("'") and "'" in value[1:]:
            return value[1:value.index("'", 1)].strip() or None
        if raw_line.lstrip().startswith('"'):
            value = value.split('"', 1)[0]  # the closing quote of the dplist line
        value = value.split(";", 1)[0]
        # The name may contain spaces, but ends where a further "Key=" assignment starts.
        value = re.split(r"\s+[A-Za-z_][\w.]*\s*=", value, maxsplit=1)[0]
        return value.strip().rstrip(",").strip() or None
    return None


def iec_string(text: str) -> str:
    """Quote ``text`` as an IEC 61131-3 string literal."""
    return "'" + text.replace("$", "$$").replace("'", "$'") + "'"


def write_ust_name(new_root: Path, ust_name: str, chooser: FileChooser | None, warnings: list[str]) -> Path | None:
    """Write ``ust_name`` to UST_Name in the VisuTask's Variables.var. Problems become warnings."""
    candidates = sorted(
        (path for path in new_root.rglob("*") if path.is_file() and path.name.casefold() == VISU_VARIABLES_FILE
         and path.parent.name.casefold() == VISU_TASK_FOLDER),
        key=lambda path: (len(path.parts), str(path).casefold()))
    logical = [path for path in candidates if any(part.casefold() == "logical" for part in path.relative_to(new_root).parts)]
    candidates = logical or candidates
    if not candidates:
        warnings.append(tr("warn_no_visu_variables", root=new_root))
        return None
    target = candidates[0]
    if len(candidates) > 1:
        target = chooser(VISU_VARIABLES_FILE, new_root, candidates) if chooser else None
        if target is None:
            warnings.append(tr("warn_ust_name_not_written", name=ust_name))
            return None
    text, encoding = read_text_with_encoding(target)
    match = UST_NAME_DECLARATION_RE.search(text)
    if match is None:
        warnings.append(tr("warn_no_ust_name_declaration", path=target))
        return None
    if match.group("length") and len(ust_name) > int(match.group("length")):
        warnings.append(tr("warn_ust_name_too_long", name=ust_name, length=match.group("length")))
    try:
        ust_name.encode(encoding)
    except UnicodeEncodeError:
        encoding = "utf-8-sig"
    replacement = f"{match.group('head')} := {iec_string(ust_name)};"
    target.write_text(text[:match.start()] + replacement + text[match.end():], encoding=encoding, newline="")
    return target


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


def copy_ladder_tasks(old_root: Path, new_root: Path, chooser: FileChooser | None = None) -> LadderCopyResult:
    """Copy every task containing .ld files into the new project, keeping the folder structure.

    Missing package folders are created and each parent Package.pkg is extended
    so that Automation Studio shows the copied packages and programs.  Each task
    is then added to the same task class in the Cpu.sw of the new project's
    active configuration as in the old project.
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
    relatives = [task.relative_to(old_logical) for task in tasks]
    deployed, warnings = deploy_tasks(old_root, new_root, relatives, chooser)
    return LadderCopyResult(relatives, copied, overwritten, packages_updated, deployed, warnings)


SW_TASK_CLASS_RE = re.compile(r'<TaskClass\b(?P<attributes>[^>]*?)(?:/>|>(?P<body>.*?)</TaskClass>)', re.DOTALL)
SW_TASK_RE = re.compile(r'^[ \t]*<Task\b[^>]*?(?:/>|>.*?</Task>)', re.DOTALL | re.MULTILINE)


def task_source(relative: Path) -> str:
    """Return the Cpu.sw ``Source`` of a program, e.g. ``Bk2_Anwendung.Allgemein.AL00.prg``."""
    return ".".join(relative.parts) + ".prg"


def deploy_tasks(old_root: Path, new_root: Path, tasks: list[Path],
                 chooser: FileChooser | None) -> tuple[list[tuple[str, str]], list[str]]:
    """Add the copied tasks to the same task classes of the new Cpu.sw as in the old one.

    Returns the deployed (task, task class) pairs and warnings.
    """
    try:
        old_sw = configuration_cpu(active_configuration(project_root(old_root), chooser)) / "Cpu.sw"
        new_sw = configuration_cpu(active_configuration(project_root(new_root), chooser)) / "Cpu.sw"
    except ConversionError as error:
        return [], [tr("warn_no_deployment", error=error)]
    if not old_sw.exists() or not new_sw.exists():
        return [], [tr("warn_no_deployment", error=old_sw if not old_sw.exists() else new_sw)]

    wanted = {task_source(task).casefold() for task in tasks}
    # Old deployment in file order: (task class, task element, name, source).
    old_tasks: list[tuple[str, str, str, str]] = []
    for task_class in SW_TASK_CLASS_RE.finditer(read_text(old_sw)):
        class_name = xml_attribute(task_class.group("attributes"), "Name")
        for task in SW_TASK_RE.finditer(task_class.group("body") or ""):
            element = task.group(0).strip()
            source = xml_attribute(element, "Source")
            if source.casefold() in wanted:
                old_tasks.append((class_name, element, xml_attribute(element, "Name"), source))
    deployed_sources = {source.casefold() for _, _, _, source in old_tasks}
    warnings = [tr("warn_task_not_deployed", task=task) for task in tasks
                if task_source(task).casefold() not in deployed_sources]

    text, encoding = read_text_with_encoding(new_sw)
    newline = "\r\n" if "\r\n" in text else "\n"
    deployed: list[tuple[str, str]] = []
    for class_name, element, name, source in old_tasks:
        if any(xml_attribute(task.group(0), "Source").casefold() == source.casefold()
               for task in SW_TASK_RE.finditer(text)):
            continue  # already deployed in the new project
        if any(xml_attribute(task.group(0), "Name").casefold() == name.casefold() for task in SW_TASK_RE.finditer(text)):
            warnings.append(tr("warn_task_name_in_use", task=name))
            continue
        target = next((match for match in SW_TASK_CLASS_RE.finditer(text)
                       if xml_attribute(match.group("attributes"), "Name") == class_name), None)
        if target is None:
            warnings.append(tr("warn_task_class_missing", task=name, task_class=class_name))
            continue
        line_start = text.rfind("\n", 0, target.start()) + 1
        indent = re.match(r"[ \t]*", text[line_start:]).group(0)
        task_line = f"{indent}  {element}{newline}"
        if target.group("body") is None or "\n" not in target.group("body"):
            # Expand <TaskClass ... /> (or one written on a single line) into a multi-line element.
            body = (target.group("body") or "").strip()
            replacement = (f"<TaskClass{target.group('attributes').rstrip()}>{newline}"
                           + (f"{indent}  {body}{newline}" if body else "")
                           + f"{task_line}{indent}</TaskClass>")
            text = text[:target.start()] + replacement + text[target.end():]
        else:
            # Insert as the last task, at the start of the line holding </TaskClass>.
            closing = target.end() - len("</TaskClass>")
            insert_at = text.rfind("\n", 0, closing) + 1
            text = text[:insert_at] + task_line + text[insert_at:]
        deployed.append((name, class_name))
    if deployed:
        new_sw.write_text(text, encoding=encoding, newline="")
    return deployed, warnings


HW_MODULE_RE = re.compile(r'^[ \t]*<Module\b[^>]*?(?:/>|>.*?</Module>)[ \t]*\r?\n?', re.DOTALL | re.MULTILINE)
HW_CONNECTION_RE = re.compile(r'<Connection\b[^>]*\bConnector="(?P<connector>[^"]*)"[^>]*'
                              r'\bTargetModule="(?P<target>[^"]*)"[^>]*\bTargetConnector="(?P<target_connector>[^"]*)"')
IO_MAPPING_RE = re.compile(r'\bAT\s+%[A-Z]+\s*\.\s*"(?P<module>[^"]+)"', re.IGNORECASE)
# Connectors that only join modules mechanically (bus base, terminal block, power
# supply slot).  Every other connector (X2X, POWERLINK, Ethernet, ...) is a bus.
MECHANICAL_CONNECTOR_RE = re.compile(r"^(?:SL|SS|PS)\d*$", re.IGNORECASE)


@dataclass
class HardwareModule:
    name: str
    type: str
    text: str
    connections: list[tuple[str, str, str]]


def xml_attribute(element: str, name: str) -> str:
    match = re.search(rf'\b{name}="([^"]*)"', element)
    return match.group(1) if match else ""


def parse_hardware(text: str) -> dict[str, HardwareModule]:
    modules: dict[str, HardwareModule] = {}
    for match in HW_MODULE_RE.finditer(text):
        block = match.group(0)
        start_tag = block[:block.index(">") + 1]
        name = xml_attribute(start_tag, "Name")
        connections = [(c.group("connector"), c.group("target"), c.group("target_connector"))
                       for c in HW_CONNECTION_RE.finditer(block)]
        modules[name] = HardwareModule(name, xml_attribute(start_tag, "Type"), block, connections)
    return modules


def physical_configurations(project: Path) -> list[Path]:
    physical = project / "Physical"
    package = physical / "Physical.pkg"
    if not package.exists():
        raise ConversionError(tr("no_physical", root=project))
    names = re.findall(r'<Object\b[^>]*Type="Configuration"[^>]*>\s*([^<]+?)\s*</Object>', read_text(package))
    return [physical / name for name in names if (physical / name / "Hardware.hw").exists()]


def active_configuration(project: Path, chooser: FileChooser | None) -> Path:
    """Return the active hardware configuration folder of an Automation Studio project."""
    configurations = physical_configurations(project)
    if not configurations:
        raise ConversionError(tr("no_configuration", root=project))
    settings = project / "LastUser.set"
    if settings.exists():
        active = xml_attribute(read_text(settings), "ActiveConfigurationName")
        for configuration in configurations:
            if configuration.name.casefold() == active.casefold():
                return configuration
    if len(configurations) == 1:
        return configurations[0]
    if chooser is None:
        raise ConversionError(tr("ambiguous_configuration", root=project))
    selected = chooser("Hardware.hw", project, [configuration / "Hardware.hw" for configuration in configurations])
    if selected is None:
        raise ConversionError(tr("choice_cancelled", filename="Hardware.hw"))
    return selected.parent


def configuration_cpu(configuration: Path) -> Path:
    package = read_text(configuration / "Config.pkg")
    match = re.search(r'<Object\b[^>]*Type="Cpu"[^>]*>\s*([^<]+?)\s*</Object>', package)
    if match is None or not (configuration / match.group(1)).is_dir():
        raise ConversionError(tr("no_cpu", root=configuration))
    return configuration / match.group(1)


def project_root(selection: Path) -> Path:
    """Accept a project folder or a folder below it (e.g. Logical) and return the project folder."""
    selection = selection.resolve()
    for folder in (selection, *selection.parents):
        if (folder / "Physical" / "Physical.pkg").exists():
            return folder
    raise ConversionError(tr("no_physical", root=selection))


def select_io_modules(modules: dict[str, HardwareModule], cpu: str) -> list[str]:
    """Return the X20 I/O modules connected to ``cpu`` over a bus, excluding the CPU itself.

    Modules that are only attached mechanically to the CPU (bus base, power
    supply, interface modules and their terminal blocks) belong to the PLC and
    are left out.  Everything reached over a bus connector (X2X, POWERLINK,
    Ethernet ...) is I/O, including bus couplers (X20BC...) with their modules.
    """
    edges: dict[str, list[tuple[str, bool]]] = {name: [] for name in modules}
    for module in modules.values():
        for connector, target, _ in module.connections:
            if target in modules:
                mechanical = bool(MECHANICAL_CONNECTOR_RE.match(connector))
                edges[module.name].append((target, mechanical))
                edges[target].append((module.name, mechanical))
    plc = {cpu}
    pending = [cpu]
    while pending:
        for neighbour, mechanical in edges[pending.pop()]:
            if mechanical and neighbour not in plc:
                plc.add(neighbour)
                pending.append(neighbour)
    is_x20 = lambda name: modules[name].type.upper().startswith("X20")
    selected: set[str] = set()
    pending = [neighbour for name in plc for neighbour, mechanical in edges[name]
               if not mechanical and neighbour not in plc and is_x20(neighbour)]
    while pending:
        name = pending.pop()
        if name in selected:
            continue
        selected.add(name)
        pending.extend(neighbour for neighbour, _ in edges[name]
                       if neighbour not in plc and neighbour not in selected and is_x20(neighbour))
    return sorted(selected)


def copy_io_configuration(old_root: Path, new_root: Path, chooser: FileChooser | None = None) -> IoCopyResult:
    """Copy the X20 I/O modules and their I/O mapping into the new project's active configuration."""
    old_project, new_project = project_root(old_root), project_root(new_root)
    old_configuration = active_configuration(old_project, chooser)
    new_configuration = active_configuration(new_project, chooser)
    old_cpu_folder, new_cpu_folder = configuration_cpu(old_configuration), configuration_cpu(new_configuration)
    old_cpu, new_cpu = old_cpu_folder.name, new_cpu_folder.name

    old_modules = parse_hardware(read_text(old_configuration / "Hardware.hw"))
    new_hardware = new_configuration / "Hardware.hw"
    new_text, new_encoding = read_text_with_encoding(new_hardware)
    new_modules = parse_hardware(new_text)
    if old_cpu not in old_modules:
        raise ConversionError(tr("cpu_not_in_hardware", cpu=old_cpu, path=old_configuration / "Hardware.hw"))
    if new_cpu not in new_modules:
        raise ConversionError(tr("cpu_not_in_hardware", cpu=new_cpu, path=new_hardware))
    selected = select_io_modules(old_modules, old_cpu)
    if not selected:
        raise ConversionError(tr("no_io_modules", path=old_configuration / "Hardware.hw"))
    if new_cpu in selected:
        raise ConversionError(tr("io_name_is_cpu", name=new_cpu))

    warnings: list[str] = []
    target_re = re.compile(rf'(\bTargetModule="){re.escape(old_cpu)}(")')
    copied_blocks: dict[str, str] = {}
    for name in selected:
        module = old_modules[name]
        for connector, target, target_connector in module.connections:
            if target in old_modules and target not in selected and target != old_cpu:
                warnings.append(tr("warn_io_target_skipped", module=name, connector=connector, target=target))
        copied_blocks[name] = target_re.sub(rf"\g<1>{new_cpu}\g<2>", module.text)

    # Bus connectors of the new CPU that the copied modules now occupy.
    used = {(target_connector.casefold()) for name in selected
            for _, target, target_connector in old_modules[name].connections if target == old_cpu}
    for module in new_modules.values():
        if module.name in copied_blocks:
            continue
        for _, target, target_connector in module.connections:
            if target == new_cpu and target_connector.casefold() in used:
                warnings.append(tr("warn_connector_in_use", module=module.name, connector=target_connector, cpu=new_cpu))

    replaced = sum(name in new_modules for name in copied_blocks)
    blocks = {name: module.text for name, module in new_modules.items() if name not in copied_blocks}
    newline = "\r\n" if "\r\n" in new_text else "\n"
    for name, block in copied_blocks.items():
        block = block.replace("\r\n", "\n").replace("\n", newline)
        blocks[name] = block if block.endswith(newline) else block + newline
    first = HW_MODULE_RE.search(new_text)
    closing = new_text.rfind("</Hardware>")
    if closing < 0:
        raise ConversionError(tr("invalid_hardware", path=new_hardware))
    head = new_text[:first.start()] if first else new_text[:closing]
    body = "".join(blocks[name] for name in sorted(blocks))
    new_hardware.write_text(head + body + new_text[closing:], encoding=new_encoding, newline="")

    copied_mappings, cpu_lines, skipped = merge_io_mapping(old_cpu_folder / "IoMap.iom", new_cpu_folder / "IoMap.iom",
                                                           set(selected), old_cpu, new_cpu)
    for line in cpu_lines:
        warnings.append(tr("warn_cpu_mapping", line=line, cpu=new_cpu))
    for line in skipped:
        warnings.append(tr("warn_mapping_skipped", line=line))
    return IoCopyResult(new_hardware, new_cpu_folder / "IoMap.iom", selected, replaced, copied_mappings, warnings)


def merge_io_mapping(old_map: Path, new_map: Path, modules: set[str], old_cpu: str,
                     new_cpu: str) -> tuple[int, list[str], list[str]]:
    """Copy the mapping lines of ``modules`` and of the old CPU into the new IoMap.iom.

    Mappings of the old CPU are renamed to the new CPU.  Existing lines for the
    copied modules are replaced and CPU lines already present are not added
    again, so running twice is harmless.  Returns the number of copied lines,
    the copied CPU lines and the old lines that were not copied.
    """
    if not old_map.exists():
        return 0, [], []
    if new_map.exists():
        text, encoding = read_text_with_encoding(new_map)
    else:
        text, encoding = "VAR_CONFIG\r\nEND_VAR\r\n", "utf-8"
    lines = [line for line in text.splitlines()
             if not ((match := IO_MAPPING_RE.search(line)) and match.group("module") in modules)]
    present = {line.strip() for line in lines}
    copied: list[str] = []
    cpu_lines: list[str] = []
    skipped: list[str] = []
    for line in read_text(old_map).splitlines():
        match = IO_MAPPING_RE.search(line)
        if not match:
            continue
        line = line.strip()
        if match.group("module") == old_cpu:
            line = line.replace(f'"{old_cpu}"', f'"{new_cpu}"', 1)
            cpu_lines.append(line)
            if line in present:
                continue
        elif match.group("module") not in modules:
            skipped.append(line)
            continue
        copied.append("\t" + line)
    newline = "\r\n" if "\r\n" in text else "\n"
    if not copied:
        return 0, cpu_lines, skipped
    end = next((index for index in range(len(lines) - 1, -1, -1) if END_VAR_RE.match(lines[index])), None)
    if end is None:
        lines += ["VAR_CONFIG", *copied, "END_VAR"]
    else:
        lines[end:end] = copied
    new_map.write_text(newline.join(lines) + newline, encoding=encoding, newline="")
    return len(copied), cpu_lines, skipped


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
        self.copy_io = tk.BooleanVar(value=False)
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
        self.rowconfigure(5, weight=1)

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
            row=3, column=0, columnspan=2, pady=(12, 2), sticky="w")
        self._translate(ttk.Checkbutton(self, variable=self.copy_io), "copy_io").grid(
            row=4, column=0, columnspan=2, pady=(2, 10), sticky="w")
        self._translate(ttk.Button(self, command=self.run_conversion), "convert").grid(
            row=4, column=2, pady=(2, 10), sticky="e")
        self.log = scrolledtext.ScrolledText(self, height=15, wrap=tk.WORD, state="disabled")
        self.log.grid(row=5, column=0, columnspan=3, sticky="nsew")
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
        if result.ust_target:
            summary += tr("ust_written", name=result.ust_name, path=result.ust_target)
        if result.warnings:
            summary += tr("warnings") + "\n- " + "\n- ".join(result.warnings) + "\n"
        if self.copy_ladder.get():
            try:
                ladder = copy_ladder_tasks(old_root, new_root, self.choose_file)
            except (ConversionError, OSError) as error:
                self.write_log(summary + tr("ladder_error", error=error))
                messagebox.showerror(tr("ladder_failed"), f"{summary}\n{error}")
                return
            self.write_log(tr("ladder_tasks") + "\n- " + "\n- ".join(str(task) for task in ladder.tasks) + "\n")
            summary += tr("ladder_summary", tasks=len(ladder.tasks), files=ladder.files_copied,
                          overwritten=ladder.files_overwritten, packages=len(ladder.packages_updated))
            summary += tr("ladder_deployed", count=len(ladder.deployed))
            if ladder.warnings:
                summary += tr("warnings") + "\n- " + "\n- ".join(ladder.warnings) + "\n"
        if self.copy_io.get():
            try:
                io = copy_io_configuration(old_root, new_root, self.choose_file)
            except (ConversionError, OSError) as error:
                self.write_log(summary + tr("io_error", error=error))
                messagebox.showerror(tr("io_failed"), f"{summary}\n{error}")
                return
            summary += tr("io_summary", modules=len(io.modules), replaced=io.modules_replaced,
                          mappings=io.mappings_copied, hardware=io.hardware)
            if io.warnings:
                summary += tr("warnings") + "\n- " + "\n- ".join(io.warnings) + "\n"
        self.write_log(summary)
        messagebox.showinfo(tr("conversion_complete"), summary)


def main() -> None:
    root = tk.Tk()
    ConverterApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
