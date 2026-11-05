HorizonHaptics HID transport and trigger encoders
=================================================
Source: https://github.com/haritha99ch/HorizonHaptics
Revision: 79fbe2fd7a56e21bd101867dbf14718f2e91ffab
MIT license; copyright 2026 Haritha Rathnayake (see LICENSE).

Adaptations: no connection pulse; trigger-only output (preserves lightbar,
player LEDs and body rumble); discovery errors retry; avoid sleeping under
producer lock; close handles after failed setup; launcher-specific error text;
lazy package initialization; expose transport errors for launcher status;
skip unchanged reports (#692).
The FH1 packet receiver, lifecycle and resistance mapping
are local code. No FH6 parser, UI, updater or audio dependencies are bundled.
