"""Package-boundary smoke tests."""

import skid2
import skid2.application
import skid2.backend
import skid2.contracts
import skid2.persistence
import skid2.protocol
import skid2.reference
import skid2.sim


def test_package_boundaries_import() -> None:
    """All intentional package boundaries import without path manipulation."""

    assert skid2.__version__ == "1.0.0"
    assert skid2.application.__doc__
    assert skid2.backend.__doc__
    assert skid2.contracts.__doc__
    assert skid2.persistence.__doc__
    assert skid2.protocol.__doc__
    assert skid2.reference.__doc__
    assert skid2.sim.__doc__
