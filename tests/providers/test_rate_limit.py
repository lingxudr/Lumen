import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from server.hybrid_providers.rate_limit import ProviderRateLimiter
import threading
import time


def test_concurrency_cap():
    lim = ProviderRateLimiter(limits={"test": 2}, min_interval={"test": 0})
    active = 0
    max_active = 0
    lock = threading.Lock()

    def worker():
        nonlocal active, max_active
        with lim.acquire("test"):
            with lock:
                active += 1
                max_active = max(max_active, active)
            time.sleep(0.05)
            with lock:
                active -= 1

    threads = [threading.Thread(target=worker) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert max_active <= 2


if __name__ == "__main__":
    test_concurrency_cap()
    print("rate_limit ok")
