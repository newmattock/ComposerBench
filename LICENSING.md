# Licensing status

ComposerBench is published as source for inspection. The original project metadata declares `Proprietary-until-license-audit`. This publication preserves that status and does not grant an MIT, Apache, GPL, or other open-source license for ComposerBench.

No MAGDA, Tracktion Engine, JUCE, third-party plugin binaries, model weights, or sample libraries are bundled in this repository. ComposerBench's adapters communicate with a separately installed MAGDA executable. The engine changes ComposerBench relies on (the `serve` session) are published separately under MAGDA's GPL-3.0 terms in the `composerbench-serve` branch of [newmattock/magda-core](https://github.com/newmattock/magda-core/tree/composerbench-serve).

The inspected MAGDA checkout carries GPLv3 terms. Its Tracktion Engine dependency lists GPL/commercial licensing; JUCE lists AGPLv3/commercial licensing. These dependencies have their own terms, which must be considered separately for a combined distribution. Python and FFmpeg dependencies are installed separately and retain their respective licenses.

The source publication is not a claim that a bundled or commercial distribution has completed a licensing audit.
