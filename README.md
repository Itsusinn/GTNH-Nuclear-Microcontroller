# OC Automation for Actively Cooled Nuclear Reactors

This solution operates reliably in low-TPS environments and allows restarting the game server while it is running.

There are no placement restrictions; the containers mentioned simply need to be adjacent to the microcontroller.

**Supported fuel rods**: Quad Thorium, Quad Uranium, Quad High-Density Uranium, Quad MOX, Quad High-Density Plutonium, Quad Naquadria, "Core", Quad Tiberium, Quad Excited Uranium, Quad Excited Plutonium

- Supports hybrid rods: "Core" + Excited Uranium, "Core" + Excited Plutonium

**Supported coolant cells**: 3 types of helium coolant cells, 3 types of NaK coolant cells, 4 types of space coolant cells, Neutronium Heat Capacitor

**Limitations**:

- Only supports six-chamber actively-cooled configurations (missing a reactor chamber will cause an error)

- Only one type of fuel rod and one type of coolant cell (or the hybrid rods above) may be used; otherwise an error occurs

- Coolant cell durability consumption per second must not exceed 10% (a mismatch between heat generation and heat capacity will cause an error)

- When using MOX fuel rods, manual preheating is required

## Setup

- Save the code from `nuclear.lua` to a file on your OC computer.

- Insert a blank EEPROM into the computer, run `flash <filename>`, and follow the instructions to flash the code onto the EEPROM.

- In an Electronics Assembler, place the following in order: T1 Microcontroller Case, Transposer, T1 Redstone Card, T1 CPU, T1 RAM, and the flashed EEPROM; then click "Assemble".

Note: The EEPROM has only 4 KB of storage. The source file (`nuclear_SourceCode.lua`) is too large; you must use the compressed code (`nuclear.lua`) that employs variable renaming and other minifications.

## Usage

- The microcontroller requires power (it can be powered via an ME Interface).

- An unexpected condition latches a fault: reactor scheduling stops, while the microcontroller keeps running to hold the alarm output. Fix the fault, then restart the controller manually. Removing or restoring the control signal does not clear the latch. Since the program remains running, this state does not trigger the front indicator's program-error flashing.

- On a fault, the controller first turns off all reactors, then continuously holds a strength-15 redstone signal toward sides confirmed to have no reactor. Place an IC2 Nuclear Control Howler Alarm next to the controller, or connect it through a dedicated redstone circuit. Outputs are reapplied every game-time second without clearing them between writes; temporary redstone failures are retried. Restarting the program clears the alarm output. Normal control-signal shutdowns, replacements and retry waits do not emit this alarm signal.

- Prefer an alarm above or below the controller, on a side without a reactor. Until horizontal orientation is identified, fault handling only enables safe vertical sides. Horizontal alarms may also briefly sound during the initial orientation probes. Keep alarm wiring separate from reactors and other independently controlled devices: all confirmed non-reactor sides emit a signal on a fault.

- Alarming requires controller power and a working redstone component. Power loss, redstone failure, or a component failure before the inventory scan finishes cannot guarantee an alarm. Before that scan completes, fault handling only attempts to turn off all outputs.

- During replacement, insufficient output space (including a full ME Interface) or a shortage of usable coolant cells triggers up to three retries after the initial failure, one game-time second apart. The affected reactor stays off while waiting. If the issue persists after the third retry, the controller latches a fault and holds the alarm output. Successful replacement during the retries is checked again before restarting the reactor.

- The program will not start the nuclear reactor on its own; applying a redstone signal of any strength to the microcontroller will start it.

- Removing the redstone control signal during operation schedules shutdown after 10 game-time seconds. Restoring the signal cancels shutdown; removing it again starts a new countdown. Checks and replacements continue during the countdown, and replacement stops are immediate.

- The redstone control signal must not be passed directly to the reactor; turning the reactor on and off should be left to the microcontroller.

- A single microcontroller can control multiple reactors, but **do not** let the microcontroller touch two faces of the same reactor.

- The microcontroller must be placed adjacent to a reactor chamber. It supports the following two operating modes.

**1. Regular Container Mode**

- During startup, the microcontroller scans nearby containers. If no ME Interface is found, it automatically enters this mode. Scanning is performed only once at startup.

- Directions containing fuel rods or coolant cells with durability **above 30** are marked as input directions (there can be multiple).

- One container direction that contains neither fuel rods nor coolant cells with durability above 30 is marked as the output direction.

**2. ME Mode**

- During startup, the microcontroller scans nearby containers. If an ME Interface or ME Dual Interface is found, it automatically enters this mode. Scanning is performed only once at startup.

- Both input and output of fuel rods and coolant cells are handled through this ME Interface.


# Code Compression Process

Use a Cargo single-file script; building requires no Python, Node.js or global `luamin` installation:

```sh
rustup toolchain install nightly
cargo +nightly -Zscript compress.rs
```

Dependency versions are embedded in `compress.rs`. Cargo downloads and compiles them on the first run, then reuses its cache.
Default paths are relative to the script directory. Override them with `--source <path>` and `--output <path>`.
Run `cargo +nightly -Zscript compress.rs --check` to verify the committed image without writing it.

The build checks source syntax using Lua 5.2, then uses `luck_minifier` to shorten local names and remove comments and unnecessary whitespace.
Global names, numeric literals and execution flow are preserved; constant folding and inlining are disabled.
Repeated text is packed into a dictionary with printable markers absent from the source and decoded in a single `gsub` pass.
The build chooses short quotes or long brackets to minimize safe string literals, without reserving the old marker characters.

Before atomically replacing `nuclear.lua`, the build verifies a byte-for-byte decoding round trip,
checks the generated Lua 5.2 syntax, and enforces the 4096-byte EEPROM limit using UTF-8 byte counts.
Failures preserve the previous image.

To run component simulations and compression regression tests:

```sh
cargo +nightly -Zscript test --manifest-path compress.rs
python -m pip install -r requirements-dev.txt
python -m unittest discover -s tests
```

The simulations run both source and EEPROM code on Lua 5.2, 5.3 and 5.4;
they do not replace in-game reactor testing.
