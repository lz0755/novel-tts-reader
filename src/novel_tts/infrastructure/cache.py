class CacheIndex:
    def set_quota(self, size):
        if size < 0:
            raise ValueError("缓存限额不可为负")
        with self.db:
            self.db.execute(
                "INSERT OR REPLACE INTO preferences VALUES('cache_bytes',?)",
                (str(size),),
            )

    def cache_over_quota(self):
        limit = self.db.execute(
            "SELECT value FROM preferences WHERE name='cache_bytes'"
        ).fetchone()
        quota = int(limit[0]) if limit else 1024**3
        used = self.db.execute(
            "SELECT coalesce(sum(frames*2+80),0) FROM audio"
        ).fetchone()[0]
        return quota if used > quota else None

    def entries(self):
        return [
            tuple(r)
            for r in self.db.execute("SELECT hash,digest FROM audio ORDER BY last_used")
        ]

    def forget_audio(self, key):
        with self.db:
            self.db.execute("DELETE FROM audio WHERE hash=?", (key,))

    def recover(self):
        folder = self.paths.cache
        index = dict(self.entries())
        stats = dict(partial=0, orphan=0, missing=0, corrupt=0)
        with self.db:
            self.db.execute("UPDATE jobs SET state='interrupted' WHERE state='running'")
            for path in folder.iterdir():
                if path.is_symlink() or not path.is_file():
                    continue
                kind = None
                if path.suffix == ".partial":
                    kind = "partial"
                elif path.suffix == ".wav" and path.stem not in index:
                    kind = "orphan"
                if kind:
                    try:
                        path.unlink()
                        stats[kind] += 1
                    except PermissionError:
                        pass
            for key, digest in index.items():
                path = folder / (key + ".wav")
                if not path.is_file():
                    self.db.execute("DELETE FROM audio WHERE hash=?", (key,))
                    stats["missing"] += 1
        return stats
