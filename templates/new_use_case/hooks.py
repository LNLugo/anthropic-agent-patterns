"""Optional glue: only what use_case.yaml can't express.

build() calls configure(uc) last, with the assembled use case. Most use cases need
nothing here; keep it empty unless a test shows the configuration falls short.
"""


def configure(uc) -> None:
    return None
