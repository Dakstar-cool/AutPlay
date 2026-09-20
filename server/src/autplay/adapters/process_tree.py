"""OS containment for a fixed child which cannot create descendants before GO."""

import subprocess
from typing import Protocol


class ProcessTree(Protocol):
    def attach(self, process: subprocess.Popen[bytes]) -> None:
        """Attach the retained exact process handle before permitting any child work."""
        ...

    def request_stop(self) -> None:
        """Latch stop before termination; later attachment must remain forbidden."""
        ...

    def seal_if_empty(self) -> bytes | None:
        """Forbid future attachment and return stable evidence only if the tree is empty.

        The caller separately proves spawn finished and its exact root process exited.
        """
        ...

    def close_after_exit(self) -> None:
        """Release OS ownership only after the tree has been sealed and observed empty."""
        ...
