"""AI pipeline as replaceable modules.

The application is never hard-coded to one model. Each stage is an interface;
production swaps in YOLO/ONNX detectors, a real tracker, and a face model
without touching the API or event logic. The *reference* implementations here
run with no GPU and no model weights so the full pipeline is demonstrable and
testable; they are clearly marked as placeholders.
"""
