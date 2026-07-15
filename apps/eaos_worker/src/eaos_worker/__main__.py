"""Allow ``python -m eaos_worker`` to start the evolution worker.

Delegates to ``main.main()`` which sets up logging, loads AppConfig, and
runs the async worker loop.
"""

from eaos_worker.main import main

if __name__ == "__main__":
    main()
