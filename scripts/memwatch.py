"""Peak-memory sampler: polls RSS of a pid and its children via ps."""
import subprocess, time, threading, os


def _rss_kb(pid):
    try:
        out = subprocess.run(["ps", "-o", "rss=", "-p", str(pid)],
                             capture_output=True, text=True).stdout.strip()
        return int(out.split()[0]) if out else 0
    except Exception:
        return 0


def _descendants(pid):
    try:
        out = subprocess.run(["pgrep", "-P", str(pid)], capture_output=True, text=True).stdout.split()
        kids = []
        for k in out:
            kids.append(int(k))
            kids += _descendants(int(k))
        return kids
    except Exception:
        return []


def peak_self_tree_gb():
    """Peak RSS of self + waited-for children, via getrusage (no ps needed)."""
    import resource
    r = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    c = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
    # macOS reports bytes; normalize either unit
    def gb(v):
        return v / (1024 ** 3) if v > 1e7 else v / (1024 ** 2)
    return max(gb(r), gb(c))


class PeakMem:
    """Track peak total RSS (bytes) of a process tree."""

    def __init__(self, pid, interval=0.05):
        self.pid = pid
        self.interval = interval
        self.peak = 0
        self._stop = threading.Event()
        self._t = threading.Thread(target=self._run, daemon=True)

    def _run(self):
        while not self._stop.is_set():
            pids = [self.pid] + _descendants(self.pid)
            tot = sum(_rss_kb(p) for p in pids)
            if tot > self.peak:
                self.peak = tot
            time.sleep(self.interval)

    def start(self):
        self._t.start()
        return self

    def stop(self):
        self._stop.set()
        self._t.join(timeout=2)
        return self

    def gb(self):
        return max(self.peak / (1024 ** 2), peak_self_tree_gb())
