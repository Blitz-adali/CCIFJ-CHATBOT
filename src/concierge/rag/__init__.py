from .src.rag_search import search
from .src.config import EMBED_MODEL_NAME


def index(*args, **kwargs):
    from .src.index_init import index as build_index
    return build_index(*args, **kwargs)


__all__ = ["search", "index", "EMBED_MODEL_NAME"]
