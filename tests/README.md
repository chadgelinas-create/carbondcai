# Test Suite Organization

Tests are grouped by the production boundary they verify. New tests should be placed with the
subsystem they exercise instead of being grouped by ticket number.

| Directory | Responsibility |
| --- | --- |
| `tests/sim/` | Device contracts, deterministic runtime, and transport-independent behavior |
| `tests/protocol/` | Protocol adapters and generated-metadata interpretation |
| `tests/architecture/` | Dependency direction, framework neutrality, and clock boundaries |

The remaining root-level tests predate this organization and can move incrementally when their
owning subsystem is changed. `tests/conftest.py` remains at the root so its shared fixtures apply
to every test directory.

P4-004 focused tests are:

```text
tests/sim/test_command_processing.py
tests/protocol/test_command_policy.py
tests/sim/test_device_model.py
tests/architecture/test_dependency_boundaries.py
tests/sim/test_simulator_runtime.py
```


```
