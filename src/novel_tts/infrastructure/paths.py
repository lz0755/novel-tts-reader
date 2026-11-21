from pathlib import Path
from platformdirs import user_cache_path, user_data_path


class Paths:
    def __init__(self, root: Path | None = None):
        self.data = (
            root.resolve()
            if root
            else user_data_path("NovelTTSReader", appauthor=False)
        )
        self.cache = (
            self.data / "cache"
            if root
            else user_cache_path("NovelTTSReader", appauthor=False) / "audio"
        )
        self.data.mkdir(exist_ok=True, parents=True)
        self.cache.mkdir(exist_ok=True, parents=True)


RESOURCES = Path(__file__).resolve().parents[1] / "resources"
