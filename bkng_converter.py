"""Convert legacy Bk2 datapoint definitions into a BkNG ``global.var`` file.

The program has no third-party dependencies.  Run it with::

    python bkng_converter.py

Use the two folder buttons to select the old and new Automation Studio project
folders, then click *Convert*. The selected new ``global.var`` is replaced.
"""

from __future__ import annotations

import re
import sys
import tkinter as tk
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext, ttk
from typing import Iterable

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


class ConversionError(RuntimeError):
    """Raised when project files cannot be discovered or converted safely."""


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


def discover_file(root: Path, filename: str) -> Path:
    matches = sorted(
        (path for path in root.rglob("*") if path.is_file() and path.name.casefold() == filename.casefold()),
        key=lambda path: (len(path.relative_to(root).parts), str(path).casefold()),
    )
    if not matches:
        raise ConversionError(f"Could not find '{filename}' below:\n{root}")
    # Automation Studio keeps generated copies below Temp.  Prefer the editable
    # source file below Logical when a project root is selected.
    logical_matches = [path for path in matches if any(part.casefold() == "logical" for part in path.relative_to(root).parts)]
    if len(logical_matches) == 1:
        return logical_matches[0]
    if len(matches) > 1:
        choices = "\n".join(str(path.relative_to(root)) for path in matches)
        raise ConversionError(
            f"More than one '{filename}' was found below {root}.\n"
            f"Please select a folder that contains exactly one copy:\n{choices}"
        )
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


def convert(old_root: Path, new_root: Path) -> ConversionResult:
    old_root, new_root = old_root.resolve(), new_root.resolve()
    if not old_root.is_dir() or not new_root.is_dir():
        raise ConversionError("Both selections must be existing project directories.")
    old_files = {name: discover_file(old_root, name) for name in REQUIRED_OLD_FILES}
    target = discover_file(new_root, "global.var")

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
        group = groups.get(name.casefold())
        if group is None and name.casefold() != "uststatus":
            warnings.append(f"{name}: no group found; Gruppe was omitted.")
        output.append(render_datapoint(declaration, datapoint, group,
                                      datapoint.block_defaults.get(type_block_name(declaration.group("type")), [])))
        converted += 1

    declared_names = {
        match.group("name").casefold()
        for match in map(GENERIC_VARIABLE_RE.match, old_global.splitlines()) if match
    }
    unmatched = len(set(datapoints) - declared_names)
    if unmatched:
        warnings.append(f"{unmatched} datapoint(s) in dplist.dat have no declaration in the old global.var.")
    if not output:
        raise ConversionError("No variable declarations were found in the old global.var.")
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


class ConverterApp(ttk.Frame):
    def __init__(self, master: tk.Tk) -> None:
        super().__init__(master, padding=14)
        self.old_path = tk.StringVar()
        self.new_path = tk.StringVar()
        master.title("BkNG Converter V4")
        if ICON_PATH.exists():
            master.iconbitmap(default=str(ICON_PATH))
        master.minsize(700, 400)
        self.grid(sticky="nsew")
        master.columnconfigure(0, weight=1)
        master.rowconfigure(0, weight=1)
        self.columnconfigure(1, weight=1)
        self.rowconfigure(3, weight=1)
        self._path_row(0, "Old project folder", self.old_path, self.choose_old)
        self._path_row(1, "New project folder", self.new_path, self.choose_new)
        ttk.Button(self, text="Convert", command=self.run_conversion).grid(row=2, column=1, pady=(12, 10), sticky="e")
        self.log = scrolledtext.ScrolledText(self, height=15, wrap=tk.WORD, state="disabled")
        self.log.grid(row=3, column=0, columnspan=3, sticky="nsew")
        self.write_log("Select the old and new project folders, then click Convert.\n")

    def _path_row(self, row: int, label: str, variable: tk.StringVar, command: object) -> None:
        ttk.Label(self, text=label + ":").grid(row=row, column=0, padx=(0, 8), pady=5, sticky="w")
        ttk.Entry(self, textvariable=variable).grid(row=row, column=1, pady=5, sticky="ew")
        ttk.Button(self, text="Browse…", command=command).grid(row=row, column=2, padx=(8, 0), pady=5)

    def choose_old(self) -> None:
        selection = filedialog.askdirectory(title="Select old project directory")
        if selection:
            self.old_path.set(selection)

    def choose_new(self) -> None:
        selection = filedialog.askdirectory(title="Select new project directory")
        if selection:
            self.new_path.set(selection)

    def write_log(self, text: str) -> None:
        self.log.configure(state="normal")
        self.log.insert(tk.END, text)
        self.log.see(tk.END)
        self.log.configure(state="disabled")

    def run_conversion(self) -> None:
        try:
            result = convert(Path(self.old_path.get()), Path(self.new_path.get()))
        except (ConversionError, OSError) as error:
            self.write_log(f"ERROR: {error}\n")
            messagebox.showerror("Conversion failed", str(error))
            return
        summary = (
            f"Converted {result.converted} datapoint declaration(s); copied {result.copied} unchanged.\n"
            f"Written: {result.target}\n"
        )
        if result.warnings:
            summary += "Warnings:\n- " + "\n- ".join(result.warnings) + "\n"
        self.write_log(summary)
        messagebox.showinfo("Conversion complete", summary)


def main() -> None:
    root = tk.Tk()
    ConverterApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
