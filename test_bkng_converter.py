import tempfile
import unittest
from pathlib import Path

from bkng_converter import ConversionError, convert


class ConverterTests(unittest.TestCase):
    def write(self, root: Path, relative: str, content: str) -> None:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

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


if __name__ == "__main__":
    unittest.main()
