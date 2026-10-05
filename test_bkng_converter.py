import re
import tempfile
import unittest
from pathlib import Path

from bkng_converter import (TEXTS, ConversionError, convert, copy_io_configuration, copy_ladder_tasks,
                            parse_hardware, parse_ust_name, project_dir_from_file, set_language)


class ConverterTests(unittest.TestCase):
    def setUp(self) -> None:
        # Message assertions below are written against the English texts.
        set_language("en")
        self.addCleanup(set_language, "de")
    def write(self, root: Path, relative: str, content: str) -> None:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def write_raw(self, root: Path, relative: str, content: str) -> None:
        """Write without newline translation, e.g. to keep CRLF exactly as given."""
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content.encode("utf-8"))

    def test_converts_datapoints_adds_defaults_and_copies_others(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            old, new = root / "old", root / "new"
            old.mkdir()
            new.mkdir()
            self.write(old, "nested/global.var", "VAR_GLOBAL\nAP_ATemp : BkAp; (*old*)\nOther : INT; (*keep*)\nEND_VAR\n")
            self.write(old, "data/dplist.dat", '''"$AP"\n"#PAB Kp=200 Int=I Default=9"\n"#VIS W.Kpos=3"\n"@AP_ATemp", ;BkAp\n"~Eth Aussentemp. UST 0703"\n"#PAB OG=9999 UG=-500"\n"#VIS Wert.Einh=°C Wert.Kpos=1"\n"#VI1 ignored=1"\n"#UVIS page=0"\n''')
            self.write(old, "data/gruppen.dat", '"$Allgemein"\n"@AP_ATemp", ;BkAp\n')
            self.write(new, "target/global.var", "old target\n")

            result = convert(old, new)
            output = result.target.read_text(encoding="utf-8")

            self.assertEqual(result.converted, 1)
            self.assertEqual(result.copied, 1)
            self.assertEqual(list(result.target.parent.glob("*.bak")), [])
            self.assertTrue(output.startswith("VAR\n"))
            self.assertTrue(output.endswith("END_VAR\n"))
            self.assertEqual(output.count("VisUstStat : ARRAY[0..7] OF BOOL;"), 1)
            self.assertIn("AP_ATemp : BkAp := (Unit:='°C',Kpos:=1,Klartext:='Eth Aussentemp. UST 0703',OG:=9999,UG:=-500,Kp:=200,Int:=1,Default:=9,Gruppe:='Allgemein'); (*Eth Aussentemp. UST 0703*)", output)
            self.assertIn("Other : INT; (*keep*)", output)

    def test_rk_has_special_kpos_mapping(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            old, new = root / "old", root / "new"
            old.mkdir()
            new.mkdir()
            self.write(old, "global.var", "VAR\nRK_Test : BkRk;\nEND_VAR\n")
            self.write(old, "dplist.dat", '"@RK_Test"\n"#VIS W.Kpos=1 Y.Kpos=2"\n')
            self.write(old, "gruppen.dat", '"$G"\n"@RK_Test"\n')
            self.write(new, "global.var", "target\n")
            result = convert(old, new)
            self.assertIn("KposXW:=1,KposY:=2,Gruppe:='G'", result.target.read_text(encoding="utf-8"))

    def test_uses_logical_source_and_preserves_full_group_name(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            old, new = root / "old", root / "new"
            old.mkdir()
            new.mkdir()
            self.write(old, "Logical/Global.var", "VAR\nAP_Test : BkAp;\nEND_VAR\n")
            self.write(old, "Logical/dplist.dat", '"@AP_Test"\n"~Test"\n')
            self.write(old, "Logical/gruppen.dat", '"$Stat. Hzg Ost West"\n"@AP_Test"\n')
            # This is a generated duplicate that must not make conversion fail.
            self.write(old, "Temp/gruppen/gruppen.dat", '"$Wrong"\n"@AP_Test"\n')
            self.write(new, "Logical/Global.var", "target\n")

            result = convert(old, new)
            self.assertIn("Gruppe:='Stat. Hzg Ost West'", result.target.read_text(encoding="utf-8"))

    def test_maps_real_project_unit_fields_and_copies_complex_declarations(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            old, new = root / "old", root / "new"
            old.mkdir()
            new.mkdir()
            self.write(old, "Global.var", "VAR\nFlags : ARRAY[0..7] OF BOOL;\nRK_Test : BkRk;\nSW_Test : BkSw;\nEND_VAR\n")
            self.write(old, "dplist.dat", '''"@RK_Test"\n"~RK"\n"#PAB W=2 Xw=-2"\n"#VIS W.Einh=C Y.Einh=% W.Kpos=1 Y.Kpos=2"\n"@SW_Test"\n"~SW"\n"#PAB SW=1"\n"#VIS SW.Einh=bar SW.Kpos=2"\n''')
            self.write(old, "gruppen.dat", '"$Allgemein"\n"@RK_Test"\n"@SW_Test"\n')
            self.write(new, "Global.var", "target\n")

            result = convert(old, new)
            output = result.target.read_text(encoding="utf-8")
            self.assertIn("Flags : ARRAY[0..7] OF BOOL;", output)
            self.assertIn("Unit:='C',UnitY:='%',KposXW:=1,KposY:=2", output)
            self.assertIn("SW_Test : BkSw := (Unit:='bar',Kpos:=2,Klartext:='SW'", output)
            self.assertNotIn("W:=2", output)
            self.assertNotIn("Xw:=-2", output)
            self.assertNotIn("SW:=1", output)

    def test_uststatus_without_group_does_not_warn(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            old, new = root / "old", root / "new"
            old.mkdir()
            new.mkdir()
            self.write(old, "Global.var", "VAR\nUstStatus : BkStat;\nEND_VAR\n")
            self.write(old, "dplist.dat", '"@UstStatus"\n"~Status"\n')
            self.write(old, "gruppen.dat", '"$Allgemein"\n')
            self.write(new, "Global.var", "VAR\nEND_VAR\n")

            result = convert(old, new)
            self.assertEqual(result.warnings, [])
            self.assertIn("UstStatus : BkStat := (Klartext:='Status');", result.target.read_text(encoding="utf-8"))

    def test_bkstat_never_gets_group_even_if_listed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            old, new = root / "old", root / "new"
            self.write(old, "Global.var", "VAR\nST_Pumpe : BkStat;\nAP_Test : BkAp;\nEND_VAR\n")
            self.write(old, "dplist.dat", '"@ST_Pumpe"\n"~Pumpe"\n"@AP_Test"\n"~Test"\n')
            self.write(old, "gruppen.dat", '"$Allgemein"\n"@ST_Pumpe"\n"@AP_Test"\n')
            self.write(new, "Global.var", "VAR\nEND_VAR\n")

            result = convert(old, new)
            text = result.target.read_text(encoding="utf-8")
            self.assertEqual(result.warnings, [])
            self.assertIn("ST_Pumpe : BkStat := (Klartext:='Pumpe');", text)
            self.assertIn("AP_Test : BkAp := (Klartext:='Test',Gruppe:='Allgemein');", text)

    def test_visuststat_is_written_once_at_the_start(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            old, new = root / "old", root / "new"
            old.mkdir()
            new.mkdir()
            self.write(old, "Global.var", "VAR\nVisUstStat : ARRAY[0..7] OF BOOL;\nOther : INT;\nEND_VAR\n")
            self.write(old, "dplist.dat", "")
            self.write(old, "gruppen.dat", "")
            self.write(new, "Global.var", "VAR\nEND_VAR\n")

            result = convert(old, new)
            lines = result.target.read_text(encoding="utf-8").splitlines()
            self.assertEqual(lines[:2], ["VAR", "VisUstStat : ARRAY[0..7] OF BOOL;"])
            self.assertEqual(lines.count("VisUstStat : ARRAY[0..7] OF BOOL;"), 1)

    def test_rk_uses_missing_parameters_from_its_block_definition(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            old, new = root / "old", root / "new"
            old.mkdir()
            new.mkdir()
            self.write(old, "Global.var", "VAR\nRK_DiffDruckStatHzgOW : BkRk;\nEND_VAR\n")
            self.write(old, "dplist.dat", '''"$RK"\n"#PAB Kp=237 Ti=100 Int=I"\n"#VIS W.Einh=C W.Kpos=1 Y.Kpos=1"\n"#VI1 W.Einh=C W.Kpos=1 Y.Kpos=1"\n"@RK_DiffDruckStatHzgOW"\n"~Differenzdruckregelung StatHzg Ow"\n"#PAB W=50 Ti=1000 Ymin=0 Ymax=1000 Hand=A Int=I XWG=1000 OG=9999 UG=0 Xw=1"\n"#VIS W.Einh=bar Y.Einh=% W.Kpos=2 Y.Kpos=1 W.MAX=9999 W.MIN=-9999"\n''')
            self.write(old, "gruppen.dat", '"$Allgemein"\n"@RK_DiffDruckStatHzgOW"\n')
            self.write(new, "Global.var", "VAR\nEND_VAR\n")

            result = convert(old, new)
            output = result.target.read_text(encoding="utf-8")
            self.assertIn("Kp:=237", output)
            self.assertIn("Ti:=1000", output)
            self.assertIn("Int:=1", output)
            self.assertIn("Unit:='bar'", output)

    def test_preserves_constant_and_retain_sections(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            old, new = root / "old", root / "new"
            old.mkdir()
            new.mkdir()
            self.write(
                old,
                "Global.var",
                "VAR\n"
                "AP_Test : BkAp;\n"
                "END_VAR\n"
                "VAR CONSTANT\n"
                "Ventil_WWB1 : STRING[80] := 'Ventil WWB01';\n"
                "END_VAR\n"
                "VAR RETAIN\n"
                "S_HZG_Std : ARRAY[0..47] OF WertZeit;\n"
                "END_VAR\n"
                "VAR\n"
                "Other : INT;\n"
                "END_VAR\n",
            )
            self.write(old, "dplist.dat", '"@AP_Test"\n"~Test"\n')
            self.write(old, "gruppen.dat", '"$Allgemein"\n"@AP_Test"\n')
            self.write(new, "Global.var", "target\n")

            result = convert(old, new)
            output = result.target.read_text(encoding="utf-8")
            lines = output.splitlines()

            # Every section header from the old file must reappear, with its
            # qualifier intact, and each declaration must stay inside its
            # original section rather than being merged into one VAR block.
            self.assertEqual(lines.count("VAR"), 2)
            self.assertEqual(lines.count("VAR CONSTANT"), 1)
            self.assertEqual(lines.count("VAR RETAIN"), 1)
            self.assertEqual(lines.count("END_VAR"), 4)
            self.assertEqual(lines.count("VisUstStat : ARRAY[0..7] OF BOOL;"), 1)

            constant_idx = lines.index("VAR CONSTANT")
            self.assertEqual(lines[constant_idx + 1], "Ventil_WWB1 : STRING[80] := 'Ventil WWB01';")
            self.assertEqual(lines[constant_idx + 2], "END_VAR")

            retain_idx = lines.index("VAR RETAIN")
            self.assertEqual(lines[retain_idx + 1], "S_HZG_Std : ARRAY[0..47] OF WertZeit;")
            self.assertEqual(lines[retain_idx + 2], "END_VAR")

            self.assertIn("AP_Test : BkAp := (Klartext:='Test',Gruppe:='Allgemein'); (*Test*)", output)
            self.assertIn("Other : INT;", output)

    def test_datapoint_with_no_parameters_has_no_empty_initializer(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            old, new = root / "old", root / "new"
            old.mkdir()
            new.mkdir()
            self.write(old, "Global.var", "VAR\nUstStatus : BkStat;\nEND_VAR\n")
            self.write(old, "dplist.dat", '"@UstStatus"\n')
            self.write(old, "gruppen.dat", "")
            self.write(new, "Global.var", "VAR\nEND_VAR\n")

            result = convert(old, new)
            output = result.target.read_text(encoding="utf-8")
            self.assertIn("UstStatus : BkStat;", output)
            self.assertNotIn(":= ()", output)

    def test_block_default_with_dropped_key_does_not_crash(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            old, new = root / "old", root / "new"
            old.mkdir()
            new.mkdir()
            self.write(old, "Global.var", "VAR\nRK_Test : BkRk;\nEND_VAR\n")
            # "W" is a bare (undotted) legacy field that BkNG's BkRk has no
            # settable equivalent for and must be dropped, even when it
            # arrives via a block default rather than an explicit parameter.
            self.write(old, "dplist.dat", '"$RK"\n"#PAB W=100 Kp=200"\n"@RK_Test"\n"~Test"\n')
            self.write(old, "gruppen.dat", '"$G"\n"@RK_Test"\n')
            self.write(new, "Global.var", "VAR\nEND_VAR\n")

            result = convert(old, new)
            output = result.target.read_text(encoding="utf-8")
            self.assertIn("Kp:=200", output)
            self.assertNotIn("W:=100", output)

    def test_later_type_block_replaces_earlier_defaults_for_following_datapoints(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            old, new = root / "old", root / "new"
            old.mkdir()
            new.mkdir()
            self.write(old, "Global.var", "VAR\nRK_First : BkRk;\nRK_Second : BkRk;\nEND_VAR\n")
            self.write(old, "dplist.dat", '''"$RK"\n"#PAB Kp=200 Ti=100 Int=I"\n"@RK_First"\n"~First"\n"#PAB Ti=500"\n"$RK"\n"#PAB Kp=237 Ti=100 Int=I"\n"@RK_Second"\n"~Second"\n"#PAB Ti=1000"\n''')
            self.write(old, "gruppen.dat", '"$Allgemein"\n"@RK_First"\n"@RK_Second"\n')
            self.write(new, "Global.var", "VAR\nEND_VAR\n")

            result = convert(old, new)
            output = result.target.read_text(encoding="utf-8")
            first = next(line for line in output.splitlines() if line.startswith("RK_First"))
            second = next(line for line in output.splitlines() if line.startswith("RK_Second"))
            self.assertIn("Kp:=200", first)
            self.assertIn("Ti:=500", first)
            self.assertIn("Kp:=237", second)
            self.assertIn("Ti:=1000", second)

    def test_ambiguous_files_are_resolved_by_chooser(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            old, new = root / "old", root / "new"
            self.write(old, "a/Global.var", "VAR\nAP_Wrong : BkAp;\nEND_VAR\n")
            self.write(old, "b/Global.var", "VAR\nAP_Right : BkAp;\nEND_VAR\n")
            self.write(old, "dplist.dat", '"@AP_Right"\n"~Right"\n')
            self.write(old, "gruppen.dat", '"$G"\n"@AP_Right"\n')
            self.write(new, "x/Global.var", "target\n")
            self.write(new, "y/Global.var", "target\n")
            asked: list[tuple[str, list[Path]]] = []

            def chooser(filename: str, search_root: Path, candidates: list[Path]) -> Path:
                asked.append((filename, candidates))
                return next(path for path in candidates if path.parent.name in {"b", "y"})

            result = convert(old, new, chooser)

            self.assertEqual([name for name, _ in asked], ["global.var", "global.var"])
            self.assertTrue(all(path.is_absolute() for _, paths in asked for path in paths))
            self.assertEqual(result.target, (new / "y/Global.var").resolve())
            self.assertIn("AP_Right : BkAp := (Klartext:='Right'", result.target.read_text(encoding="utf-8"))
            self.assertEqual((new / "x/Global.var").read_text(encoding="utf-8"), "target\n")

    def test_ambiguous_files_cancelled_or_without_chooser_raise(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            old, new = root / "old", root / "new"
            self.write(old, "a/dplist.dat", "")
            self.write(old, "b/dplist.dat", "")
            self.write(old, "gruppen.dat", "")
            self.write(old, "Global.var", "VAR\nEND_VAR\n")
            self.write(new, "Global.var", "target\n")

            with self.assertRaisesRegex(ConversionError, "More than one"):
                convert(old, new)
            with self.assertRaisesRegex(ConversionError, "cancelled"):
                convert(old, new, lambda *_: None)


    def test_copies_ladder_tasks_with_structure_and_package_entries(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            old, new = root / "old", root / "new"
            pkg = '<?xml version="1.0" encoding="utf-8"?>\r\n<Package xmlns="http://br-automation.co.at/AS/Package">\r\n  <Objects>\r\n{}  </Objects>\r\n</Package>'
            self.write_raw(old, "Logical/Package.pkg", pkg.format(
                '    <Object Type="Package" Description="Anwendung">App</Object>\r\n'
                '    <Object Type="Package">Other</Object>\r\n'))
            self.write_raw(old, "Logical/App/Package.pkg", pkg.format(
                '    <Object Type="Program" Language="IEC" Description="Ladder">T1</Object>\r\n'
                '    <Object Type="Program" Language="IEC">StTask</Object>\r\n'))
            self.write(old, "Logical/App/T1/IEC.prg", "prg")
            self.write(old, "Logical/App/T1/T1Cyclic.ld", "ld")
            self.write(old, "Logical/App/T1/T1.var", "var")
            self.write(old, "Logical/App/StTask/Main.st", "st")
            self.write(old, "Logical/Other/T2/T2Cyclic.LD", "ld")
            self.write(old, "Temp/Objects/T3/T3.ld", "generated")
            self.write_raw(new, "Logical/Package.pkg", pkg.format('    <Object Type="Package">Libraries</Object>\r\n'))

            result = copy_ladder_tasks(old, new)

            self.assertEqual(result.tasks, [Path("App/T1"), Path("Other/T2")])
            self.assertEqual((result.files_copied, result.files_overwritten), (4, 0))
            self.assertEqual((new / "Logical/App/T1/T1.var").read_text(encoding="utf-8"), "var")
            self.assertFalse((new / "Logical/App/StTask").exists())
            self.assertFalse((new / "Temp").exists())
            logical = (new / "Logical/Package.pkg").read_bytes().decode("utf-8-sig")
            self.assertIn('<Object Type="Package">Libraries</Object>\r\n', logical)
            self.assertIn('    <Object Type="Package" Description="Anwendung">App</Object>\r\n  </Objects>', logical.replace(
                '    <Object Type="Package">Other</Object>\r\n', ""))
            app = (new / "Logical/App/Package.pkg").read_bytes().decode("utf-8-sig")
            self.assertIn('<Object Type="Program" Language="IEC" Description="Ladder">T1</Object>', app)
            self.assertNotIn("StTask", app)
            self.assertIn('<Object Type="Program" Language="IEC">T2</Object>',
                          (new / "Logical/Other/Package.pkg").read_bytes().decode("utf-8-sig"))

            again = copy_ladder_tasks(old, new)
            self.assertEqual((again.files_overwritten, again.packages_updated), (4, []))
            self.assertEqual((new / "Logical/Package.pkg").read_bytes().decode("utf-8-sig").count(">App<"), 1)


    def write_project(self, root: Path, configurations: dict[str, tuple[str, str, str]], active: str) -> None:
        """Create a minimal AS project: {configuration: (cpu folder, hardware modules, IoMap.iom)}."""
        objects = "".join(f'<Object Type="Configuration">{name}</Object>\r\n' for name in configurations)
        self.write_raw(root, "Physical/Physical.pkg", f'<Physical xmlns="x"><Objects>\r\n{objects}</Objects></Physical>')
        self.write_raw(root, "LastUser.set", f'<ProjectSettings><ConfigurationManager ActiveConfigurationName="{active}" /></ProjectSettings>')
        for name, (cpu, modules, io_map) in configurations.items():
            self.write_raw(root, f"Physical/{name}/Config.pkg",
                           f'<Configuration><Objects><Object Type="Cpu">{cpu}</Object></Objects></Configuration>')
            self.write_raw(root, f"Physical/{name}/Hardware.hw",
                           '﻿<?xml version="1.0" encoding="utf-8"?>\r\n<Hardware xmlns="http://br-automation.co.at/AS/Hardware">\r\n'
                           + modules + "</Hardware>")
            self.write_raw(root, f"Physical/{name}/{cpu}/IoMap.iom", io_map)

    def test_copies_x20_io_modules_and_mapping_into_active_configuration(self) -> None:
        def module(name: str, type_: str, *connections: tuple[str, str, str]) -> str:
            lines = "".join(f'    <Connection Connector="{c}" TargetModule="{t}" TargetConnector="{tc}" />\r\n'
                            for c, t, tc in connections)
            return f'  <Module Name="{name}" Type="{type_}" Version="1.0.0.0">\r\n{lines}  </Module>\r\n'

        old_modules = "".join([
            module("PLC", "X20CP1485-1", ("SL", "BBcpu", "SL1")),
            module("BBcpu", "X20BB80"),
            module("PScpu", "X20PS9600", ("PS", "BBcpu", "PS1"), ("SS1", "TBps", "SS")),
            module("TBps", "X20TB12"),
            module("IF1", "X20IF1082", ("SS", "PLC", "SS1")),
            module("BM", "X20BM11", ("X2X1", "PLC", "IF6")),
            module("DI", "X20DI9371", ("SL", "BM", "SL1"), ("SS1", "TBdi", "SS")),
            module("TBdi", "X20TB12"),
            module("BC", "X20BC0083", ("PLK1", "PLC", "IF3"), ("SL", "BBbc", "SL1")),
            module("BBbc", "X20BB80"),
            module("PSbc", "X20PS9400", ("PS", "BBbc", "PS1")),
            module("BMbc", "X20BM11", ("X2X1", "BBbc", "X2X2")),
            module("DO", "X20DO9322", ("SL", "BMbc", "SL1")),
            module("OpcUa", "OpcUa_any", ("ETH1", "PLC", "IF2")),
        ])
        old_map = ('VAR_CONFIG\n\tin1 AT %IX."DI".DigitalInput01;\n\tout1 AT %QX."DO".DigitalOutput01;\n'
                   '\tbat AT %IB."PLC".BatteryStatusCPU;\n\tok AT %IX."OpcUa".ModuleOk;\nEND_VAR\n')
        new_modules = module("NEWCPU", "X20CP3686X") + module("DI", "X20DI9371", ("SL", "Gone", "SL1"))
        new_map = 'VAR_CONFIG\r\n\tkeep AT %IX."NEWCPU".Something;\r\n\told AT %IX."DI".DigitalInput02;\r\nEND_VAR\r\n'
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            old, new = root / "old", root / "new"
            self.write_project(old, {"Cfg": ("PLC", old_modules, old_map)}, active="Cfg")
            self.write_project(new, {"A": ("CPU_A", module("CPU_A", "X20CP0484"), ""),
                                     "B": ("NEWCPU", new_modules, new_map)}, active="B")
            (new / "Logical").mkdir()

            result = copy_io_configuration(old, new / "Logical")

            self.assertEqual(result.modules, ["BBbc", "BC", "BM", "BMbc", "DI", "DO", "PSbc", "TBdi"])
            self.assertEqual((result.modules_replaced, result.mappings_copied), (1, 3))
            hardware = parse_hardware((new / "Physical/B/Hardware.hw").read_bytes().decode("utf-8-sig"))
            self.assertEqual(sorted(hardware), sorted(["NEWCPU", *result.modules]))
            self.assertIn(("X2X1", "NEWCPU", "IF6"), hardware["BM"].connections)
            self.assertIn(("PLK1", "NEWCPU", "IF3"), hardware["BC"].connections)
            self.assertEqual(hardware["DI"].connections, [("SL", "BM", "SL1"), ("SS1", "TBdi", "SS")])
            io_map = (new / "Physical/B/NEWCPU/IoMap.iom").read_bytes().decode("utf-8")
            expected_map = ('VAR_CONFIG\r\n\tkeep AT %IX."NEWCPU".Something;\r\n'
                            '\tin1 AT %IX."DI".DigitalInput01;\r\n\tout1 AT %QX."DO".DigitalOutput01;\r\n'
                            '\tbat AT %IB."NEWCPU".BatteryStatusCPU;\r\nEND_VAR\r\n')
            self.assertEqual(io_map, expected_map)
            self.assertEqual(len(result.warnings), 2)
            self.assertIn("CPU mapping taken over for NEWCPU", result.warnings[0])
            self.assertIn('ok AT %IX."OpcUa".ModuleOk;', result.warnings[1])
            self.assertNotIn("BC", (new / "Physical/A/Hardware.hw").read_text(encoding="utf-8"))

            again = copy_io_configuration(old, new)
            self.assertEqual(again.mappings_copied, 2)
            self.assertEqual((new / "Physical/B/NEWCPU/IoMap.iom").read_bytes().decode("utf-8").count("BatteryStatusCPU"), 1)

    def test_copied_ladder_tasks_are_added_to_the_same_task_class(self) -> None:
        def sw(*classes: str) -> str:
            return ('﻿<?xml version="1.0" encoding="utf-8"?>\r\n<SwConfiguration xmlns="x">\r\n'
                    + "".join(classes) + "  <Libraries />\r\n</SwConfiguration>")

        def task(name: str, source: str, extra: str = "") -> str:
            return f'    <Task Name="{name}" Source="{source}" Memory="UserROM"{extra} Language="IEC" />\r\n'

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            old, new = root / "old", root / "new"
            for project, cpu in ((old, "OLDCPU"), (new, "NEWCPU")):
                self.write_project(project, {"Cfg": (cpu, f'  <Module Name="{cpu}" Type="X20CP" />\r\n', "")}, "Cfg")
            (new / "Logical").mkdir()
            for name in ("T1", "T2", "T3", "T4", "T5"):
                self.write(old, f"Logical/App/{name}/{name}.ld", "ld")
            self.write_raw(old, "Physical/Cfg/OLDCPU/Cpu.sw", sw(
                '  <TaskClass Name="Cyclic#1">\r\n' + task("T1", "App.T1.prg", ' Description="first"')
                + task("Other", "Other.prg") + "  </TaskClass>\r\n",
                '  <TaskClass Name="Cyclic#2">\r\n' + task("T2", "App.T2.prg") + task("T5", "App.T5.prg") + "  </TaskClass>\r\n",
                '  <TaskClass Name="Cyclic#9">\r\n' + task("T3", "App.T3.prg") + "  </TaskClass>\r\n"))
            self.write_raw(new, "Physical/Cfg/NEWCPU/Cpu.sw", sw(
                '  <TaskClass Name="Cyclic#1">\r\n' + task("Visu", "System.Visu.prg") + "  </TaskClass>\r\n",
                '  <TaskClass Name="Cyclic#2" />\r\n') .replace("</SwConfiguration>", "")
                + '  <TaskClass Name="Cyclic#3">\r\n' + task("T5", "Something.else.prg") + "  </TaskClass>\r\n</SwConfiguration>")

            result = copy_ladder_tasks(old, new)

            self.assertEqual(result.deployed, [("T1", "Cyclic#1"), ("T2", "Cyclic#2")])
            new_sw = (new / "Physical/Cfg/NEWCPU/Cpu.sw").read_bytes().decode("utf-8-sig")
            self.assertIn('  <TaskClass Name="Cyclic#1">\r\n' + task("Visu", "System.Visu.prg")
                          + task("T1", "App.T1.prg", ' Description="first"') + "  </TaskClass>\r\n", new_sw)
            self.assertIn('  <TaskClass Name="Cyclic#2">\r\n' + task("T2", "App.T2.prg") + "  </TaskClass>\r\n", new_sw)
            self.assertNotIn("Other", new_sw)
            warnings = "\n".join(result.warnings)
            self.assertEqual(len(result.warnings), 3)
            self.assertIn("Task class 'Cyclic#9' does not exist", warnings)
            self.assertIn(f"{Path('App/T4')} is not assigned to a task class", warnings)
            self.assertIn("task named 'T5' already exists", warnings)

            self.assertEqual(copy_ladder_tasks(old, new).deployed, [])
            self.assertEqual((new / "Physical/Cfg/NEWCPU/Cpu.sw").read_bytes().decode("utf-8-sig"), new_sw)

    def test_parses_ust_name_from_dplist(self) -> None:
        cases = {
            '"&UST"\n;"#VIS UST.Name=Commented"\n"#VIS1 UST.Name=Other"\n"#VI1 UST.Name=Lang"\n'
            '"#VIS UST.Name=A0648"\n"#VIS UST.Name=Second"\n': "A0648",
            '"#VIS UST.Name=Halle 3 Heizung"   ; comment\n': "Halle 3 Heizung",
            '"#VIS  Ust.SprachIdx=0 UST.Name = B12 UST.Tableau=3",\n': "B12",
            "#VIS UST.Name='Quoted; Name'\n": "Quoted; Name",
            '"#VIS UST.Name="\n"#VIS UST.TableauTyp = 3"\n': None,
            '"#VI1 UST.Name=OnlyForeign"\n': None,
        }
        for dplist, expected in cases.items():
            self.assertEqual(parse_ust_name(dplist), expected, dplist)

    def test_writes_ust_name_to_visu_task_variables_only_if_found(self) -> None:
        variables = ("(*Konfigurationsvariablen*)\r\nVAR CONSTANT\r\n"
                     "\tUST_Name : STRING[80] := 'UST 1234'; (*Name, der im Header angezeigt wird. Größe*)\r\nEND_VAR\r\n")
        for dplist, expected in (('"#VIS UST.Name=Halle \'Süd\'"\n', "'Halle $'Süd$''"), ("", "'UST 1234'")):
            with tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                old, new = root / "old", root / "new"
                self.write(old, "Global.var", "VAR\nEND_VAR\n")
                self.write(old, "dplist.dat", dplist)
                self.write(old, "gruppen.dat", "")
                self.write(new, "Logical/Global.var", "target\n")
                target = new / "Logical/System/BkNg/VisuTask/Variables.var"
                target.parent.mkdir(parents=True)
                target.write_bytes(variables.encode("cp1252"))

                result = convert(old, new)

                self.assertEqual(result.warnings, [])
                self.assertEqual(result.ust_target, target.resolve() if dplist else None)
                self.assertEqual(target.read_bytes().decode("cp1252"),
                                 variables.replace("'UST 1234'", expected))

    def test_all_languages_have_the_same_texts_and_placeholders(self) -> None:
        placeholders = lambda text: sorted(re.findall(r"\{(\w+)\}", text))
        for language, texts in TEXTS.items():
            self.assertEqual(texts.keys(), TEXTS["en"].keys(), language)
            for key, text in texts.items():
                self.assertEqual(placeholders(text), placeholders(TEXTS["en"][key]), f"{language}.{key}")

    def test_project_folder_is_taken_from_apj_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.write(root, "Project/Project.apj", "<Project/>")
            self.assertEqual(project_dir_from_file(root / "Project/Project.apj"), (root / "Project").resolve())
            with self.assertRaisesRegex(ConversionError, r"\*\.apj"):
                project_dir_from_file(root / "Project")
            with self.assertRaisesRegex(ConversionError, r"\*\.apj"):
                project_dir_from_file(root / "Project/Missing.apj")

    def test_old_project_without_bk2000_files_names_the_missing_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            old, new = root / "old", root / "new"
            self.write(old, "Logical/Global.var", "VAR\nEND_VAR\n")
            self.write(old, "Logical/data/gruppen.dat", "")
            self.write(new, "Logical/Global.var", "target\n")

            with self.assertRaises(ConversionError) as context:
                convert(old, new)
            message = str(context.exception)
            self.assertIn("dplist.dat", message)
            self.assertNotIn("gruppen.dat", message)
            self.assertIn("Please select a Bk2000 project.", message)
            self.assertEqual((new / "Logical/Global.var").read_text(encoding="utf-8"), "target\n")

            (old / "Logical/data/gruppen.dat").unlink()
            with self.assertRaisesRegex(ConversionError, r"- dplist\.dat\n- gruppen\.dat"):
                convert(old, new)

    def test_errors_follow_selected_language(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            missing = Path(temp) / "missing"
            try:
                set_language("de")
                with self.assertRaisesRegex(ConversionError, "existierende Projektverzeichnisse"):
                    convert(missing, missing)
            finally:
                set_language("en")


if __name__ == "__main__":
    unittest.main()
