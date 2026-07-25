# Building Pal Admin

This document describes the confirmed build path for the Pal Admin source and
packaging review. It does not include a compiled executable, Palworld saves, or
the game-derived catalog and portrait inputs used by the maintained package.

## Confirmed build environment

The maintained Windows build was performed with the following versions:

- Python 3.14.6
- PySide6 6.11.1
- PyInstaller 6.21.0
- setuptools 83.0.0
- orjson 3.11.9
- palsav-flex 0.2.0
- palooz 0.2.0

The application and parser metadata require Python 3.10 or newer. Python 3.14.6
is the exact interpreter version recorded in the existing build environment;
using another supported Python version is not treated as an independently
validated reproduction.

## Create the build environment

From the repository root in PowerShell:

```powershell
py -3.14 -m venv .venv
& .\.venv\Scripts\python.exe -m pip install --upgrade pip
& .\.venv\Scripts\python.exe -m pip install -r requirements-build.txt
```

## Palworld save parser dependency

Pal Admin uses the `src/palsav` package from
[PalworldSaveTools](https://github.com/deafdudecomputers/PalworldSaveTools).
The exact source revision corresponding to the maintained build is:

`ea6592ebfbb79389b6f4570002c71f9b25040641`

That revision provides `palsav-flex 0.2.0` and its local `palooz 0.2.0`
dependency, both under GPL-3.0-or-later. The public repository intentionally
does not vendor this third-party source. Fetch and install the pinned checkout:

```powershell
git clone https://github.com/deafdudecomputers/PalworldSaveTools.git external\PalworldSaveTools
git -C external\PalworldSaveTools checkout ea6592ebfbb79389b6f4570002c71f9b25040641
& .\.venv\Scripts\python.exe -m pip install -e .\external\PalworldSaveTools\src\palsav\palooz
& .\.venv\Scripts\python.exe -m pip install -e .\external\PalworldSaveTools\src\palsav
```

The corresponding license text is included in
`licenses/palsav-flex-GPL-3.0-or-later.txt`. See
`THIRD_PARTY_NOTICES.md` for the provenance summary.

## Game-derived build inputs not included here

The current maintained PyInstaller specification requires these inputs for a
fully populated package at their existing paths:

- `data/catalogs/palworld-1.0-db.json`, the runtime catalog used by the editor
  and by catalog-dependent tests
- the PalCalc portrait PNG directory at
  `tools/asset-extraction/PalCalc/PalCalc.UI/Resources/Pals`

These are omitted from the proposed public repository because they are
game-derived/reference assets rather than Pal Admin source, and the portrait
package has its own attribution and licensing considerations. The small
`data/portraits/ATTRIBUTION.txt` note is retained as a pointer, but the portrait
files themselves are not.

The catalog manifest, duplicate catalog, CXX header dump, `.usmap` file,
extracted Pak assets, FModel/UE4SS tools, and the full PalCalc source tree are
not required to inspect the Pal Admin source or its PyInstaller packaging and
are also omitted. Obtain approved copies of the catalog and portraits from the
original project/data provider before attempting a fully populated local build;
do not copy a user's save or machine-specific extraction output into this
repository.

## Build the packaged application

After the parser checkout and approved data inputs are available, run:

```powershell
New-Item -ItemType Directory -Force .\out | Out-Null
$palsav = (Resolve-Path .\external\PalworldSaveTools\src\palsav).Path
& .\tools\build-paladmin.ps1 `
  -PythonPath (Resolve-Path .\.venv\Scripts\python.exe).Path `
  -PalsavPath $palsav `
  -DistPath (Join-Path (Resolve-Path .\out).Path 'dist') `
  -WorkPath (Join-Path (Resolve-Path .\out).Path 'work') `
  -LogPath (Join-Path (Resolve-Path .\out).Path 'build.log')
```

The maintained specification is `build/PalAdmin.spec`. The expected output is:

`out/dist/PalAdmin/PalAdmin.exe`

The build script passes the external parser location through `PALADMIN_PALSAV`
or its `-PalsavPath` parameter, so the unvendored public-repository layout is
supported without changing application code.

## Tests

Run the available source tests with:

```powershell
& .\.venv\Scripts\python.exe -m pytest
```

Tests that exercise the Palworld 1.0 catalog require the omitted catalog at
`data/catalogs/palworld-1.0-db.json`. The private source-boundary test for the
historical workspace layout is intentionally not part of the proposed public
test set. No real save file is required for source review, and no save should
be committed to the repository.
