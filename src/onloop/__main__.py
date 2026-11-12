"""`python -m onloop`: the same as the `onloop` command (the carrier's hook settings call it this way)."""

import sys

from .cli import main

sys.exit(main())
