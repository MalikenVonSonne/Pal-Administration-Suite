# Pal Administration Suite v1.0.0

Pal Admin is a standalone Windows Palworld save editor for players who want a
clear, reviewable way to inspect and adjust Pal records. It works offline with
local save files and keeps the editing workflow deliberately conservative:
close the game first, review the complete draft, create a verified backup,
write the changes in one transaction, and reparse the result before reporting
success.

It is an independent, unofficial tool and is not affiliated with or endorsed
by Pocketpair.

## Use

Keep the complete `PalAdmin` folder together and launch `PalAdmin.exe`. Do not
copy the executable out of its folder. Close Palworld before editing or saving.
Pal Admin blocks editing and saving while Palworld is detected running. Keep
your own independent backup of important saves. A verified automatic backup is
created before direct `Save`, and five verified backups are retained per
source. `Save a Copy` writes a separate edited file and leaves the loaded
source unchanged.

Use `Review Changes` to inspect the complete draft and `Revert Draft` to
discard pending changes. Development and user workflows use `Blueprints` for
conservative worker, ranching, combat, and general-purpose starting points.

License and third-party notices are included in `LICENSE`,
`THIRD_PARTY_NOTICES.md`, and the `licenses` directory; release packages carry
the same notices in their bundled runtime. Creator and attribution
information is in `AUTHORS.md` and `THIRD_PARTY_NOTICES.md`.

## Source and build review

The source implementation is under `project/editor`. The confirmed packaging
environment, external parser revision, omitted game-derived inputs, and build
command are documented in `BUILD.md`. Release notes, validation records, and
historical checkpoints are intentionally not part of the public source tree.
