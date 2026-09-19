"""A plugin that forgot to declare its identity."""


def preprocess(src: str) -> tuple[str, str]:
    return src, ""
