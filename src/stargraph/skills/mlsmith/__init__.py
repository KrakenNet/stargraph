# SPDX-License-Identifier: Apache-2.0
"""mlsmith — a Stargraph skill that builds runnable ML model nodes from a brief.

A *leaf* smith on the shared core (``stargraph.skills._smith``) targeting the
``MLNode`` archetype (FR-30): it emits a ``trainer.py`` that fits + serializes a
small classical-ML model (a ``build_model(path)`` entry point) for one of the two
runtimes the engine ships — ``sklearn`` (joblib) or ``onnx`` (skl2onnx export) —
plus its test. Its contract gate is the un-cheatable floor for a *model node*: it
runs the trainer to produce a real model file, pins its sha256, constructs a real
:class:`stargraph.nodes.ml.MLNode` against it, and RUNS ``execute()`` on the
fixture's input — asserting the model loads (sha-verified) and predicts the
expected value. Because the assert is on a live MLNode's prediction, a
trivially-passing generated test cannot land a trainer whose model doesn't load or
predict. Wiring the node into a full build is the orchestrator's job (Phase D).
"""
