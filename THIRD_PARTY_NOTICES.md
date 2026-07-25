# Third-party notices

## `palsav-flex`

Pal Admin bundles and uses `palsav-flex` for Palworld save parsing and writing.
It is licensed under the GNU General Public License, version 3 or later.

- Upstream project: [PalworldSaveTools](https://github.com/deafdudecomputers/PalworldSaveTools)
  `src/palsav` subtree
- Source revision used for this build: `ea6592ebfbb79389b6f4570002c71f9b25040641`
- Package versions at that revision: `palsav-flex 0.2.0`, `palooz 0.2.0`
- License text: `licenses/palsav-flex-GPL-3.0-or-later.txt`
- The public source repository obtains this dependency externally at the pinned
  revision. The local vendored copy is not included in the proposed first
  commit. See `BUILD.md`.

## PalCalc portrait resources

The optional species portrait resources are from PalCalc by Tyler Camp and
are distributed under the MIT License. They are omitted from the proposed
public source repository because they are reference assets derived from the
game. Their acquisition, attribution, and required license file are described
in `BUILD.md`.

## PySide6

The packaged interface uses PySide6 under its applicable LGPL/GPL terms. The
package is built from the PySide6 distribution and does not modify Qt.

## Palworld data

Pal Admin does not claim ownership of Palworld names, game-derived catalog
data, or game-derived imagery. The catalog is supplied for editor reference,
and the application is not affiliated with Pocketpair.
