# Building the MAGDA engine for ComposerBench

ComposerBench drives `magda_cli` from [MAGDA](https://github.com/Conceptual-Machines/magda-core)
(GPL-3.0). It needs a build that includes the **ComposerBench serve patch**: the
persistent `magda_cli serve` session and its protocol (2: bulk notes, parameter
labels, plugin persistence; 3: mixer, sends, sidechain, automation, clip edits,
master inserts, track renders and stems). Upstream MAGDA does not have it.

| | |
| --- | --- |
| Base commit | `ebd93db06e7c197922d012b2aefbdf2f073e74ff` (#2033, 2026-08-06) |
| Patch | [`newmattock/magda-core@composerbench-serve`](https://github.com/newmattock/magda-core/tree/composerbench-serve): one commit (`4962903`) on the base, 12 files under `magda/daw/` |
| Submodule pins | those of the base commit (JUCE `04bba0f`, tracktion_engine `09f2748`, faust `2e20dde`, eurorack `41d8ce0`, llama.cpp `305ba51`, juce-llm `66f853e`) |
| Protocol | 3 (`{"op":"describe"}` reports it); ComposerBench falls back to 2 and 1 with fewer features |

ComposerBench itself does not ship MAGDA code (see [LICENSING.md](../LICENSING.md)),
so the patch lives in a MAGDA fork (GPL-3.0, like MAGDA), not in this repository.

## Linux (Ubuntu 24.04)

Tested on a 2-core, 7 GB machine: about an hour for a cold Release build of
`magda_cli`; the target alone, not the app.

```sh
sudo apt-get install -y build-essential cmake ninja-build pkg-config git git-lfs \
  libgtk-3-dev libasound2-dev libjack-jackd2-dev ladspa-sdk libcurl4-openssl-dev \
  libfreetype6-dev libfontconfig1-dev libx11-dev libxcomposite-dev libxcursor-dev \
  libxext-dev libxinerama-dev libxrandr-dev libxrender-dev libwebkit2gtk-4.1-dev \
  libglu1-mesa-dev mesa-common-dev libxml2-dev zlib1g-dev libssl-dev lv2-dev liblilv-dev

git clone --branch composerbench-serve https://github.com/newmattock/magda-core ~/magda-core
cd ~/magda-core
git lfs pull --include assets/fonts                     # the CJK font is in Git LFS; configure fails on a pointer
git submodule update --init --depth 1 third_party/tracktion_engine third_party/JUCE \
  third_party/juce-llm third_party/eurorack third_party/faust third_party/llama.cpp
git -C third_party/faust submodule update --init --depth 1 libraries   # stdfaust.lib for the compiled devices

cmake -G Ninja -B build-linux -DCMAKE_BUILD_TYPE=Release -DMAGDA_BUILD_TESTS=OFF -DMAGDA_HAVE_CLAP=OFF
ninja -C build-linux magda_cli magda_plugin_scanner
# the engine looks for its plugin scanner beside itself
ln -sf ../../magda_plugin_scanner_artefacts/Release/magda_plugin_scanner \
  build-linux/magda/daw/magda_cli_artefacts/Release/magda_plugin_scanner
build-linux/magda/daw/magda_cli_artefacts/Release/magda_cli boot   # "MAGDA engine booted headless"
```

Notes from the first Linux build:

- `third_party/faust/libraries` is a submodule of the Faust submodule. Without
  it every compiled device fails with `unable to open file stdfaust.lib`.
- If Git LFS is not available, copy `assets/fonts/NotoSansCJKsc-Regular.otf`
  from another checkout (sha256 `2c76254f…5b74b`).
- Configure downloads Lua and SQLite from lua.org and sqlite.org, and the
  wasmtime C API from GitHub releases.
- Prefer Release: Debug Faust devices render several times slower.
- Hosted plugins: install them as usual (Surge XT's `.deb` puts its VST3 in
  `/usr/lib/vst3`), then `composerbench plugins scan`. Without
  `magda_plugin_scanner` beside `magda_cli` the scan finds nothing.

## macOS

The same steps with Xcode command-line tools and Homebrew `cmake ninja git-lfs`;
CLion's default `cmake-build-debug` folder is found automatically. A Debug build
needs about 10 GB of disk.

## Pointing ComposerBench at it

`composerbench.engine.discover_cli()` searches, in order: an explicit
`--cli`/`--engine`, `$COMPOSERBENCH_CLI`, then the newest `magda_cli` under
`$COMPOSERBENCH_MAGDA_ROOT` (default `~/magda-core`) in `cmake-build-debug`,
`cmake-build-release`, `build`, `build-linux` or `build-release`.

Check it end to end:

```sh
COMPOSERBENCH_CLI=... pytest -q tests/test_p2_magda_live.py tests/test_p3_protocol.py
python examples/p3_engine_smoke.py --cli $COMPOSERBENCH_CLI     # 36 engine-level checks
composerbench presets calibrate                                # refresh preset loudness for this build
```
