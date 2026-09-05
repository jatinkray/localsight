"""Entry point for `python -m apps.worker` (the documented invocation in the
README quick start). Delegates to apps.worker.main:main."""
from apps.worker.main import main

if __name__ == "__main__":
    main()
