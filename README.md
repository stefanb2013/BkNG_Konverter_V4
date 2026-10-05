# BkNG Converter V4
Run the BkNG-Globalvar-Converter.exe in the folder dist

OR

Run the desktop application with:

```powershell
python bkng_converter.py
```

Choose the Automation Studio project files (`.apj`) of the Bk2000 project and the
BkNG project. The BkNG project must be an empty BkNG project (BkNG base project).
The converter uses the folder containing each `.apj` file and searches all of its
subdirectories for these files:

- Bk2000 project: `dplist.dat`, `gruppen.dat`, and `global.var`
- BkNG project: exactly one `global.var`

If `dplist.dat` or `gruppen.dat` is missing from the Bk2000 project, the converter
lists the missing files and asks you to select a Bk2000 project.

It replaces the new `global.var` with the converted declarations. If a required
file is absent, or more than one matching file is present, conversion stops
without modifying the new project.

The program uses only Python's standard library. The included checks can be run
with `python -m unittest -v`.
