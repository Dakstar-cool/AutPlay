# SonaExecutionProfileV1

`SonaExecutionProfileV1` identifies one isolated Sona inference environment. Its
canonical JSON has exactly the fields in the checked-in golden fixture
`tests/fixtures/ml/sona-execution-profile-v1.json`. Unknown or missing fields
are invalid. The profile hash is lowercase SHA-256 of
`b"autplay.sona.execution-profile.v1\0" + RFC 8785 canonical JSON bytes`.
The fixture hash is
`56519bb2e37938b730a6912afb15eb9a4d5bde5ef70aba74012ad3e1d484a283`.

The document binds the GPU OCI image digest and source commit; Python, model
runtime, ONNX Runtime, CUDA, cuDNN and driver versions; device UUID/model and
compute capability; CUDA execution provider and options; graph optimization
level/options; precision and exact input/output tensor names, dtypes and shapes;
determinism seed and flags; thread/concurrency limits; and tokenizer, runtime
adapter and postprocessor hashes. CPU fallback is always false. A change to any
field creates a different profile digest, including a compatible driver or
provider update.

This pure codec does not attest the live process. Before any R1B/R1C inference,
the isolated GPU process must measure its actual environment, match the approved
profile, and prove provider placement and GPU admission. No production service
uses this codec yet.
