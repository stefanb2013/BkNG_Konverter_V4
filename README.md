# BkNG Converter V4
Run the BkNG-Globalvar-Converter.exe in the folder dist

OR

Run the desktop application with:

```powershell
python bkng_converter.py
```

Choose the root folders of the old and new projects. The converter searches all
subdirectories for these files:

- Old project: `dplist.dat`, `gruppen.dat`, and `global.var`
- New project: exactly one `global.var`

It replaces the new `global.var` with the converted declarations. If a required
file is absent, or more than one matching file is present, conversion stops
without modifying the new project.

The program uses only Python's standard library. The included checks can be run
with `python -m unittest -v`.
