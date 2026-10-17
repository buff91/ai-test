# Actual teaching game capture

`actual.png` is an unmodified 2200 x 1656 macOS Retina screenshot of
TeachingLab.app, captured through CUA after pressing the real `2` key.
It shows Party RPG round 2: mana 22, knight HP 56, goblins 1/11/11/11.

The capture reproduces macOS color management: rendered cyan `(103, 232, 249)`
was observed around `(138, 231, 249)`. The observer tolerates this bounded shift
while retaining window anchors, game/schema identity and a HUD checksum.
The fixture contains only the game window and is used by
`VisibleHudTests.test_actual_macos_capture_decodes_color_managed_hud`.
