"""Hermes memory — a free, offline, single-file take on tiered agent memory.

Design is inspired by HeurChain's model (https://heurchain.com): memory that
behaves like memory, not a database. We adopt its three ideas and implement
them with zero external services (no Redis/Qdrant/embedding server, no keys):

  * Three decay-weighted tiers
        self  (0.1x decay) — identity + directives. Near-permanent.
        notes (1.0x decay) — learned skills/preferences. Standard ACT-R decay.
        ops   (3.0x decay) — hot session turns. Fades fast so yesterday's
                             chatter doesn't pollute today's focus.
  * ACT-R activation — a memory's pull = recency (per-tier decay) reinforced by
    how often it's been retrieved. Reading a memory strengthens it.
  * Hybrid retrieval via Reciprocal Rank Fusion — sparse BM25 (keyword) fused
    with dense vector (cosine) rankings. BM25 needs NO model, so retrieval works
    fully offline; vectors are a bonus when an embed model is present.

Plug & play / free: only stdlib ``sqlite3`` is required. ``numpy`` (already a
daemon dep) and an Ollama embed model are optional accelerators — missing either
just drops to BM25-only. One bounded SQLite file under ``/persist/cache/llm``;
old low-activation rows are consolidated away to keep it small.

Tiers assembled per turn (identical for local and cloud):
    Core  = ``core.yml`` (the ``self`` tier), injected verbatim.
    Trend = tail of the event bus + greeter ``session.json`` (immediate recency).
    General = ACT-R + hybrid retrieval over the ``notes``/``ops`` store.

Everything is best-effort: a missing model, read-only path, or corrupt DB
degrades to "no memory for this tier" and never takes a turn down.
"""

from __future__ import annotations

import contextlib
import json
import math
import os
import re
import sqlite3
import time
from dataclasses import dataclass, field

import structlog

from cam_daemon.redact import redact

try:
    import httpx
except ImportError:                       # import-safe on bare CI
    httpx = None                          # type: ignore[assignment]

try:
    import numpy as np
    _HAVE_NUMPY = True
except ImportError:                       # vectors optional — BM25 still works
    np = None                             # type: ignore[assignment]
    _HAVE_NUMPY = False

log = structlog.get_logger("cam-daemon.memory")

# Per-tier ACT-R decay weights (higher = forgets faster), mirroring HeurChain.
_TIER_DECAY = {"self": 0.1, "notes": 1.0, "ops": 3.0}
_KIND_TIER = {
    "identity": "self", "directive": "self",
    "skill": "notes", "preference": "notes", "note": "notes", "fact": "notes",
    "turn": "ops", "session": "ops",
}
# Tiny stopword set keeps the BM25 index lean and relevant.
_STOP = frozenset(
    "a an the of to in on for and or but is are was were be been being it this that "
    "i you he she we they me my your our with as at by from up out so if then than".split()
)
_TOKEN_RE = re.compile(r"[a-z0-9]{2,}")
_RRF_K = 60                                # standard Reciprocal Rank Fusion constant


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default).strip() or default


def _flag(name: str, default: str = "1") -> bool:
    return _env(name, default) == "1"


def _tokenize(text: str) -> list[str]:
    return [t for t in _TOKEN_RE.findall(text.lower()) if t not in _STOP]


@dataclass(slots=True)
class MemoryConfig:
    enabled: bool = field(default_factory=lambda: _flag("LATHEOS_MEMORY_ENABLE", "1"))
    base_url: str = field(default_factory=lambda: _env("LATHEOS_LLM_URL", "http://127.0.0.1:11434"))
    embed_model: str = field(default_factory=lambda: _env("LATHEOS_EMBED_MODEL", "nomic-embed-text"))
    core_path: str = field(default_factory=lambda: _env("LATHEOS_CORE_MEMORY", "/persist/state/core.yml"))
    general_db: str = field(default_factory=lambda: _env("LATHEOS_GENERAL_DB", "/persist/cache/llm/general.db"))
    trend_events: str = field(default_factory=lambda: _env("LATHEOS_TREND_EVENTS", "/run/cam-daemon/events.jsonl"))
    session_file: str = field(default_factory=lambda: _env("LATHEOS_SESSION_FILE", "/persist/state/session.json"))
    trend_turns: int = field(default_factory=lambda: int(_env("LATHEOS_TREND_TURNS", "8")))
    general_k: int = field(default_factory=lambda: int(_env("LATHEOS_GENERAL_K", "4")))
    max_rows: int = field(default_factory=lambda: int(_env("LATHEOS_MEMORY_MAX_ROWS", "1500")))
    embed_timeout_s: float = 10.0
    embed_cooldown_s: float = 120.0           # after a miss, skip embeds this long


@dataclass(slots=True)
class ContextBundle:
    """Assembled memory for one turn. ``system_suffix`` is appended to the
    engine's base system prompt in HeurChain order: self -> notes -> ops."""

    core: str = ""
    general: list[str] = field(default_factory=list)
    trend: str = ""

    def system_suffix(self) -> str:
        parts: list[str] = []
        if self.core:
            parts.append("# CORE MEMORY (authoritative — obey before anything else)\n" + self.core)
        if self.general:
            parts.append("# RELEVANT MEMORY (past skills/preferences)\n" + "\n".join(f"- {s}" for s in self.general))
        if self.trend:
            parts.append("# RECENT SESSION (most recent last)\n" + self.trend)
        return "\n\n".join(parts).strip()

    def is_empty(self) -> bool:
        return not (self.core or self.general or self.trend)


class MemoryEngine:
    def __init__(self, cfg: MemoryConfig | None = None) -> None:
        self.cfg = cfg or MemoryConfig()
        # If embeddings are unavailable (model not pulled / offline) we trip a
        # short cooldown so memory ops fall back to BM25 instantly instead of
        # eating the HTTP timeout on every single call. Auto-recovers.
        self._embed_off_until = 0.0

    # -- Core (self tier) ---------------------------------------------------

    def load_core(self) -> str:
        if not self.cfg.enabled:
            return ""
        with contextlib.suppress(OSError):
            with open(self.cfg.core_path, encoding="utf-8") as fh:
                return fh.read().strip()
        return ""

    # -- Trend (immediate recency) -----------------------------------------

    def load_trend(self) -> str:
        if not self.cfg.enabled:
            return ""
        lines: list[str] = []
        with contextlib.suppress(OSError, json.JSONDecodeError):
            with open(self.cfg.session_file, encoding="utf-8") as fh:
                sess = json.load(fh)
            if isinstance(sess, dict):
                last = sess.get("last_task") or sess.get("task")
                if last:
                    lines.append(f"last task: {last}")
                todos = sess.get("todos")
                if isinstance(todos, list) and todos:
                    lines.append("open todos: " + "; ".join(str(t) for t in todos[:5]))
        with contextlib.suppress(OSError):
            with open(self.cfg.trend_events, encoding="utf-8") as fh:
                tail = fh.readlines()[-(self.cfg.trend_turns * 2):]
            for raw in tail:
                with contextlib.suppress(json.JSONDecodeError):
                    ev = json.loads(raw)
                    text = (ev.get("text") or "").strip()
                    if not text:
                        continue
                    if ev.get("type") == "user":
                        lines.append(f"user: {text}")
                    elif ev.get("type") == "cam":
                        lines.append(f"assistant: {text}")
        return "\n".join(lines[-(self.cfg.trend_turns * 2):]).strip()

    # -- General store (notes/ops tiers) -----------------------------------

    _SCHEMA = (
        "CREATE TABLE IF NOT EXISTS memories ("
        "id INTEGER PRIMARY KEY, tier TEXT, text TEXT, embedding BLOB, "
        "created REAL, last_access REAL, uses INTEGER DEFAULT 0)"
    )

    def _open(self) -> sqlite3.Connection:
        """Open + initialise the DB. Tuned for a removable drive: a 5s busy
        timeout lets the daemon, shell, and lathe-cloud share the file, and
        synchronous=FULL flushes each commit so a surprise unplug can't shear a
        half-written page."""
        conn = sqlite3.connect(self.cfg.general_db, timeout=5.0)
        try:
            conn.execute("PRAGMA busy_timeout=5000")
            conn.execute("PRAGMA synchronous=FULL")
            conn.execute(self._SCHEMA)          # canary: raises on a corrupt file
        except sqlite3.Error:
            # Release the handle before propagating so recovery can replace the
            # file (POSIX would allow it anyway; Windows/locked FS would not).
            conn.close()
            raise
        with contextlib.suppress(OSError):       # snippets can be sensitive
            os.chmod(self.cfg.general_db, 0o600)
        return conn

    def _connect(self) -> sqlite3.Connection | None:
        try:
            parent = os.path.dirname(self.cfg.general_db)
            if parent:
                os.makedirs(parent, exist_ok=True)
            return self._open()
        except sqlite3.DatabaseError as exc:
            # A drive yanked mid-write can leave the file malformed. Quarantine
            # it ONCE and rebuild empty so memory self-heals instead of failing
            # on every turn forever. Only triggers on real corruption — a plain
            # "database is locked" is retried by the busy timeout, not wiped.
            msg = str(exc).lower()
            if any(s in msg for s in ("malformed", "not a database", "file is encrypted")):
                return self._recover(exc)
            log.warning("memory.db_open_failed", error=str(exc))
            return None
        except sqlite3.Error as exc:
            log.warning("memory.db_open_failed", error=str(exc))
            return None

    def _recover(self, exc: Exception) -> sqlite3.Connection | None:
        log.warning("memory.db_corrupt_recovering", error=str(exc))
        with contextlib.suppress(OSError):
            if os.path.exists(self.cfg.general_db):
                os.replace(self.cfg.general_db, self.cfg.general_db + ".corrupt")
        try:
            return self._open()
        except sqlite3.Error as exc2:
            log.warning("memory.db_recover_failed", error=str(exc2))
            return None

    def _activation(self, tier: str, last_access: float, uses: int, now: float) -> float:
        """ACT-R-style pull: power-law recency by tier, reinforced by use count."""
        decay = _TIER_DECAY.get(tier, 1.0)
        age_days = max(0.0, (now - last_access) / 86400.0)
        recency = (1.0 + age_days) ** (-(decay * 0.4))
        return recency * (1.0 + 0.5 * math.log1p(max(0, uses)))

    async def _embed(self, text: str) -> list[float] | None:
        if httpx is None or not _HAVE_NUMPY or not text.strip():
            return None
        if time.time() < self._embed_off_until:   # in cooldown -> BM25 only
            return None
        try:
            async with httpx.AsyncClient(timeout=self.cfg.embed_timeout_s) as client:
                resp = await client.post(
                    f"{self.cfg.base_url}/api/embed",
                    json={"model": self.cfg.embed_model, "input": text},
                )
                if resp.status_code == 404:       # older Ollama API shape
                    resp = await client.post(
                        f"{self.cfg.base_url}/api/embeddings",
                        json={"model": self.cfg.embed_model, "prompt": text},
                    )
                resp.raise_for_status()
                data = resp.json()
            vecs = data.get("embeddings")
            if vecs:
                return list(vecs[0])
            vec = data.get("embedding")
            return list(vec) if vec else None
        except Exception as exc:                  # noqa: BLE001 — best-effort
            self._embed_off_until = time.time() + self.cfg.embed_cooldown_s
            log.warning("memory.embed_failed", error=str(exc), cooldown_s=self.cfg.embed_cooldown_s)
            return None

    @staticmethod
    def _bm25_order(query: str, rows: list[tuple]) -> list[int]:
        """Rank doc ids by BM25 (rows: (id, tier, text, embedding, last_access, uses))."""
        q = set(_tokenize(query))
        if not q:
            return []
        docs = [(r[0], _tokenize(r[2])) for r in rows]
        docs = [(rid, toks) for rid, toks in docs if toks]
        if not docs:
            return []
        n = len(docs)
        avgdl = sum(len(t) for _, t in docs) / n
        df: dict[str, int] = {}
        for _, toks in docs:
            for term in set(toks) & q:
                df[term] = df.get(term, 0) + 1
        if not df:
            return []
        k1, b = 1.5, 0.75
        scored: list[tuple[float, int]] = []
        for rid, toks in docs:
            tf: dict[str, int] = {}
            for t in toks:
                if t in q:
                    tf[t] = tf.get(t, 0) + 1
            if not tf:
                continue
            dl = len(toks)
            s = 0.0
            for term, f in tf.items():
                idf = math.log(1 + (n - df[term] + 0.5) / (df[term] + 0.5))
                s += idf * (f * (k1 + 1)) / (f + k1 * (1 - b + b * dl / avgdl))
            if s > 0:
                scored.append((s, rid))
        scored.sort(reverse=True)
        return [rid for _, rid in scored]

    async def _vector_order(self, query: str, rows: list[tuple]) -> list[int]:
        if not _HAVE_NUMPY:
            return []
        qvec = await self._embed(query)
        if qvec is None:
            return []
        q = np.asarray(qvec, dtype=np.float32)
        qn = float(np.linalg.norm(q)) or 1.0
        sims: list[tuple[float, int]] = []
        for r in rows:
            blob = r[3]
            if not blob:
                continue
            v = np.frombuffer(blob, dtype=np.float32)
            if v.size != q.size:
                continue
            sim = float(np.dot(q, v) / ((float(np.linalg.norm(v)) or 1.0) * qn))
            if sim > 0.2:
                sims.append((sim, r[0]))
        sims.sort(reverse=True)
        return [rid for _, rid in sims]

    async def general_search(self, query: str, k: int | None = None) -> list[str]:
        if not self.cfg.enabled or not query.strip():
            return []
        k = k or self.cfg.general_k
        conn = self._connect()
        if conn is None:
            return []
        try:
            rows = conn.execute(
                "SELECT id, tier, text, embedding, last_access, uses FROM memories"
            ).fetchall()
            if not rows:
                return []

            now = time.time()
            activation = {r[0]: self._activation(r[1], r[4], r[5], now) for r in rows}

            # Hybrid retrieval: fuse BM25 + vector rankings via RRF, then weight
            # the fused score by each memory's ACT-R activation.
            orders = [self._bm25_order(query, rows), await self._vector_order(query, rows)]
            fused: dict[int, float] = {}
            for order in orders:
                for pos, rid in enumerate(order):
                    fused[rid] = fused.get(rid, 0.0) + 1.0 / (_RRF_K + pos + 1)
            if not fused:
                return []

            ranked = sorted(fused, key=lambda rid: fused[rid] * activation.get(rid, 1.0), reverse=True)
            top = ranked[:k]

            # ACT-R reinforcement: retrieving a memory strengthens it.
            with contextlib.suppress(sqlite3.Error):
                conn.executemany(
                    "UPDATE memories SET uses = uses + 1, last_access = ? WHERE id = ?",
                    [(now, rid) for rid in top],
                )
                conn.commit()

            text_by_id = {r[0]: r[2] for r in rows}
            return [text_by_id[rid] for rid in top if rid in text_by_id]
        except sqlite3.Error as exc:
            log.warning("memory.search_failed", error=str(exc))
            return []
        finally:
            conn.close()

    async def remember(self, text: str, *, kind: str = "note") -> bool:
        """Persist a memory. Secrets are redacted before write. Best-effort."""
        if not self.cfg.enabled:
            return False
        text = redact(text.strip())[:2000]
        if not text:
            return False
        tier = _KIND_TIER.get(kind, "notes")
        vec = await self._embed(text)
        blob = np.asarray(vec, dtype=np.float32).tobytes() if (vec and _HAVE_NUMPY) else None
        conn = self._connect()
        if conn is None:
            return False
        now = time.time()
        try:
            conn.execute(
                "INSERT INTO memories (tier, text, embedding, created, last_access, uses) "
                "VALUES (?, ?, ?, ?, ?, 0)",
                (tier, text, blob, now, now),
            )
            conn.commit()
            self._consolidate(conn, now)
            return True
        except sqlite3.Error as exc:
            log.warning("memory.remember_failed", error=str(exc))
            return False
        finally:
            conn.close()

    def _consolidate(self, conn: sqlite3.Connection, now: float) -> None:
        """Keep the store small: drop the lowest-activation rows past the cap."""
        with contextlib.suppress(sqlite3.Error):
            (count,) = conn.execute("SELECT COUNT(*) FROM memories").fetchone()
            if count <= self.cfg.max_rows:
                return
            rows = conn.execute("SELECT id, tier, last_access, uses FROM memories").fetchall()
            ranked = sorted(
                rows, key=lambda r: self._activation(r[1], r[2], r[3], now), reverse=True
            )
            stale = [(r[0],) for r in ranked[self.cfg.max_rows:]]
            conn.executemany("DELETE FROM memories WHERE id = ?", stale)
            conn.commit()

    # -- Assembly -----------------------------------------------------------

    async def assemble(self, user_input: str) -> ContextBundle:
        """Build the full context for one turn. Never raises."""
        if not self.cfg.enabled:
            return ContextBundle()
        general: list[str] = []
        with contextlib.suppress(Exception):
            general = await self.general_search(user_input)
        return ContextBundle(core=self.load_core(), general=general, trend=self.load_trend())
