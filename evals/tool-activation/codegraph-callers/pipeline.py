from widget import assemble


def run_pipeline(items: list[str]) -> str:
    return assemble(items)


def unused_helper() -> str:
    return "idle"
