"""
policy.py – Loads the policy profile from policy.yaml.
"""

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path


DEFAULT_POLICY_PATH = Path("policy.yaml")
ALLOWED_PROFILES = {"strict", "balanced", "fast"}


@dataclass(frozen=True)
class PolicyConfig:
    profile: str = "balanced"
    dev_mode: bool = False
    allow_ui_override: bool = False
    classify_max_chars: int = 2000
    segment_max_pages: int = 12
    segment_chunk_max_chars: int = 12000
    segment_min_pages: int = 2
    segment_max_segments: int = 25
    segment_boundary_threshold: float = 2.4
    segment_boundary_cooldown_pages: int = 1
    segment_adaptive_threshold: bool = True
    redact_required: bool = True
    fail_closed: bool = True
    redact_company_names: bool = False
    retention_days: int | None = None
    retain_documents: bool | None = None


@dataclass(frozen=True)
class LoadedPolicy:
    config: PolicyConfig
    policy_hash: str
    source_path: Path


def _parse_scalar(value: str) -> bool | int | str | None:
    token = value.split("#", 1)[0].strip()
    if not token:
        return None

    lowered = token.lower()
    if lowered in {"true", "false"}:
        return lowered == "true"
    if lowered in {"null", "none", "~"}:
        return None

    if token.startswith(("'", '"')) and token.endswith(("'", '"')) and len(token) >= 2:
        return token[1:-1]

    try:
        return int(token)
    except ValueError:
        try:
            return float(token)
        except ValueError:
            return token


def _load_flat_yaml(path: Path) -> dict[str, bool | int | str | None]:
    data: dict[str, bool | int | str | None] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if ":" not in line:
            continue
        key, raw_value = line.split(":", 1)
        data[key.strip()] = _parse_scalar(raw_value)
    return data


def _as_bool(value: object, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1", "yes", "on"}:
            return True
        if lowered in {"false", "0", "no", "off"}:
            return False
    return default


def _as_int_or_none(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            return int(text)
        except ValueError:
            return None
    return None


def _as_float_or_none(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            return float(text)
        except ValueError:
            return None
    return None


def _validate(raw: dict[str, bool | int | str | None]) -> PolicyConfig:
    profile = str(raw.get("profile", "balanced") or "balanced").strip().lower()
    if profile not in ALLOWED_PROFILES:
        profile = "balanced"
    dev_mode = _as_bool(raw.get("dev_mode"), False)
    if profile == "fast" and not dev_mode:
        profile = "balanced"

    classify_max_chars = _as_int_or_none(raw.get("classify_max_chars"))
    if classify_max_chars is None or classify_max_chars <= 0:
        classify_max_chars = 2000
    segment_max_pages = _as_int_or_none(raw.get("segment_max_pages"))
    if segment_max_pages is None or segment_max_pages <= 0:
        segment_max_pages = 12
    segment_chunk_max_chars = _as_int_or_none(raw.get("segment_chunk_max_chars"))
    if segment_chunk_max_chars is None or segment_chunk_max_chars <= 0:
        segment_chunk_max_chars = 12000
    segment_min_pages = _as_int_or_none(raw.get("segment_min_pages"))
    if segment_min_pages is None or segment_min_pages <= 0:
        segment_min_pages = 2
    segment_max_segments = _as_int_or_none(raw.get("segment_max_segments"))
    if segment_max_segments is None or segment_max_segments <= 0:
        segment_max_segments = 25
    segment_boundary_threshold = _as_float_or_none(raw.get("segment_boundary_threshold"))
    if segment_boundary_threshold is None or segment_boundary_threshold <= 0:
        segment_boundary_threshold = 2.4
    segment_boundary_cooldown_pages = _as_int_or_none(raw.get("segment_boundary_cooldown_pages"))
    if segment_boundary_cooldown_pages is None or segment_boundary_cooldown_pages < 0:
        segment_boundary_cooldown_pages = 1
    segment_adaptive_threshold = _as_bool(raw.get("segment_adaptive_threshold"), True)

    retention_days = _as_int_or_none(raw.get("retention_days"))
    if retention_days is not None and retention_days < 0:
        retention_days = None

    return PolicyConfig(
        profile=profile,
        dev_mode=dev_mode,
        allow_ui_override=(_as_bool(raw.get("allow_ui_override"), False) if dev_mode else False),
        classify_max_chars=classify_max_chars,
        segment_max_pages=segment_max_pages,
        segment_chunk_max_chars=segment_chunk_max_chars,
        segment_min_pages=segment_min_pages,
        segment_max_segments=segment_max_segments,
        segment_boundary_threshold=segment_boundary_threshold,
        segment_boundary_cooldown_pages=segment_boundary_cooldown_pages,
        segment_adaptive_threshold=segment_adaptive_threshold,
        redact_required=_as_bool(raw.get("redact_required"), True),
        fail_closed=_as_bool(raw.get("fail_closed"), True),
        redact_company_names=_as_bool(raw.get("redact_company_names"), False),
        retention_days=retention_days,
        retain_documents=(
            raw.get("retain_documents")
            if isinstance(raw.get("retain_documents"), bool) or raw.get("retain_documents") is None
            else None
        ),
    )


def load_policy(path: str | Path = DEFAULT_POLICY_PATH) -> LoadedPolicy:
    source_path = Path(path)
    if source_path.exists():
        raw_bytes = source_path.read_bytes()
        parsed = _load_flat_yaml(source_path)
        config = _validate(parsed)
        policy_hash = sha256(raw_bytes).hexdigest()
        return LoadedPolicy(config=config, policy_hash=policy_hash, source_path=source_path)

    config = PolicyConfig()
    fallback_raw = (
        f"profile: {config.profile}\n"
        f"dev_mode: {str(config.dev_mode).lower()}\n"
        f"allow_ui_override: {str(config.allow_ui_override).lower()}\n"
        f"classify_max_chars: {config.classify_max_chars}\n"
        f"segment_max_pages: {config.segment_max_pages}\n"
        f"segment_chunk_max_chars: {config.segment_chunk_max_chars}\n"
        f"segment_min_pages: {config.segment_min_pages}\n"
        f"segment_max_segments: {config.segment_max_segments}\n"
        f"segment_boundary_threshold: {config.segment_boundary_threshold}\n"
        f"segment_boundary_cooldown_pages: {config.segment_boundary_cooldown_pages}\n"
        f"segment_adaptive_threshold: {str(config.segment_adaptive_threshold).lower()}\n"
        f"redact_required: {str(config.redact_required).lower()}\n"
        f"fail_closed: {str(config.fail_closed).lower()}\n"
    ).encode("utf-8")
    return LoadedPolicy(
        config=config,
        policy_hash=sha256(fallback_raw).hexdigest(),
        source_path=source_path,
    )
