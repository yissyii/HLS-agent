# Simulation and verification

Commit reusable verification scripts, testbench-generation rules and validation conventions here. Store generated logs, waveforms and per-task results under `output/`, not in this directory.

A task testbench must return nonzero on mismatch or runtime failure. A successful C simulation only establishes behavior for that testbench; synthesis must be checked separately.
