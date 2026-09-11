from __future__ import annotations

import multiprocessing
import os

# Keep each NumPy/librosa task single-threaded. The desktop bridge already
# bounds track-level concurrency, and nested BLAS workers can exhaust memory.
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")
os.environ.setdefault("VECLIB_MAXIMUM_THREADS", "1")

from app.soundcloud_bridge import main


if __name__ == "__main__":
    multiprocessing.freeze_support()
    raise SystemExit(main())
