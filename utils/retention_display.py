"""Human-readable labels for retention delays (stored as seconds in DB)."""


def format_retention_delay_human(seconds: int) -> str:
    if seconds <= 0:
        return "instant"
    hours = seconds / 3600.0
    if abs(hours - round(hours)) < 1e-9:
        n = int(round(hours))
        return "1 hour" if n == 1 else f"{n} hours"
    text = f"{hours:.3g}".rstrip("0").rstrip(".")
    return f"{text} hours"
