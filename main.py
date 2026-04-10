"""Legacy CLI entry point — prefer ``fastssv`` or ``python -m fastssv``."""

from fastssv.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
