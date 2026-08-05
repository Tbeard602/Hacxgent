# Firmware Matching

Firmware compatibility work is still in progress.

Planned evaluation inputs:
- exact model
- region and carrier/CSC
- current build
- bootloader revision
- anti-rollback level
- partition layout
- chipset
- package format and signatures

The matcher must never claim FRP removal from flashing unless an exact, authoritative device source proves that behavior.
