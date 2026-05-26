"""Generalisering: lossy transformaties vóór pseudonimisering (BR-B01..B05).

Elke regel is optioneel via runtime-config (`config`-tabel), net als
detectie-thresholds. `generalize_all` ketent bewust in vaste volgorde:
geboortedatum en postcodes vóór leeftijd en klinische datums, zodat
subsequentie voorspelbaar blijft voor debugging en audit-trails.

Tekst en entity-spans worden gezamenlijk bijgewerkt: na één of meer
substring-vervangingen zouden oude `start`/`end`-indexen wijzen naar
verkeerde fragmenten; daarom projecteren we alle niet-gemuteerde entities
door hetzelfde offset-schema als de vervangingen.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Final

from data.icd10_rare import RARE_ICD10_CODES
from shared.db import get_config_value
from shared.models import Entity, EntityType

logger = logging.getLogger(__name__)

_DATE_TRIPLET: Final[re.Pattern[str]] = re.compile(
    r"^(?P<d>\d{1,2})[-/](?P<m>\d{1,2})[-/](?P<y>\d{4})$"
)


def _read_bool(key: str, default: bool) -> bool:
    raw = get_config_value(key)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class GeneralizationConfig:
    """Schakelaars per BR-regel; keys in `config`-tabel."""

    birthdate: bool = True
    postcode: bool = True
    age: bool = True
    treatment_dates: bool = True
    flag_rare_icd: bool = True

    @classmethod
    def from_db(cls) -> GeneralizationConfig:
        return cls(
            birthdate=_read_bool("gen_birthdate", True),
            postcode=_read_bool("gen_postcode", True),
            age=_read_bool("gen_age", True),
            treatment_dates=_read_bool("gen_treatment_dates", True),
            flag_rare_icd=_read_bool("gen_flag_rare_icd", True),
        )


def _parse_date_triplet(raw: str) -> tuple[int, int, int] | None:
    m = _DATE_TRIPLET.match(raw.strip())
    if m is None:
        return None
    d, mth, y = int(m["d"]), int(m["m"]), int(m["y"])
    if not (1 <= mth <= 12 and 1 <= d <= 31 and 1900 <= y <= 2100):
        return None
    return d, mth, y


def _birthyear_from_entity(ent: Entity) -> int | None:
    triplet = _parse_date_triplet(ent.original)
    if triplet is None:
        logger.warning(
            "BIRTHDATE niet te parsen als DD-MM-YYYY of DD/MM/YYYY: %r",
            ent.original,
        )
        return None
    _d, _m, y = triplet
    return y


def _year_month_from_entity(ent: Entity) -> str | None:
    triplet = _parse_date_triplet(ent.original)
    if triplet is None:
        logger.warning(
            "Behandeldatum niet te parsen: %r (entity %s)",
            ent.original,
            ent.entity_type,
        )
        return None
    _d, mth, y = triplet
    return f"{y:04d}-{mth:02d}"


_OP_BEFORE_DATE: Final[re.Pattern[str]] = re.compile(r"(\s+op\s+)$", re.IGNORECASE)


def _treatment_span_and_text(text: str, ent: Entity, ym: str) -> tuple[int, int, str]:
    """Vervang ` op <datum>` door ` in YYYY-MM` waar dat in de brontekst zo staat."""
    prefix = text[max(0, ent.start - 24) : ent.start]
    alt_match = _OP_BEFORE_DATE.search(prefix)
    if alt_match is not None:
        op_start = ent.start - len(alt_match.group(1))
        return op_start, ent.end, f" in {ym}"
    return ent.start, ent.end, ym


def _normalize_icd_code(raw: str) -> str:
    """Map 'j45.0' / ' J45.0 ' naar canonical key voor set-lookup."""
    return raw.strip().upper()


def _copy_entities(entities: list[Entity]) -> list[Entity]:
    return [e.model_copy() for e in entities]


def _non_overlapping(edits: list[tuple[int, int, str, int]]) -> None:
    """Verifieer dat vervang-spannen elkaar niet kruisen (`edits` gesorteerd op start)."""
    prev_end = -1
    for start, end, _new, _idx in edits:
        if start < prev_end:
            raise ValueError(
                f"Generalisering: overlappende vervangingen op [{start}, {end}) "
                f"na eerdere edit tot {prev_end}"
            )
        prev_end = end


def _map_position(pos: int, edits: list[tuple[int, int, str]]) -> int:
    delta = 0
    for es, ee, nt in edits:
        if ee <= pos:
            delta += len(nt) - (ee - es)
    return pos + delta


def _apply_replacements(
    text: str,
    entities: list[Entity],
    plans: dict[int, tuple[int, int, str, EntityType]],
) -> tuple[str, list[Entity]]:
    """Pas substring-vervangingen toe op basis van entity-index.

    `plans` mapt entity-index → (abs_start, abs_end, new_fragment, new_entity_type).
    """
    if not plans:
        return text, _copy_entities(entities)

    edit_meta: list[tuple[int, int, int, str, EntityType]] = [
        (idx, s, e, frag, typ)
        for idx, (s, e, frag, typ) in sorted(
            plans.items(),
            key=lambda item: item[1][0],
        )
    ]
    _non_overlapping([(s, e, frag, idx) for idx, s, e, frag, _typ in edit_meta])

    slim_edits: list[tuple[int, int, str]] = [(s, e, frag) for _idx, s, e, frag, _t in edit_meta]

    chunks: list[str] = []
    last = 0
    out_pos = 0
    replaced_indices: dict[int, Entity] = {}

    for ent_idx, start, end, new_frag, new_type in edit_meta:
        chunks.append(text[last:start])
        out_pos += len(chunks[-1])
        old_ent = entities[ent_idx]
        replaced_indices[ent_idx] = old_ent.model_copy(
            update={
                "entity_type": new_type,
                "generalized_to": new_frag,
                "start": out_pos,
                "end": out_pos + len(new_frag),
                "category": None,
                "rare_icd_review": old_ent.rare_icd_review,
            }
        )
        chunks.append(new_frag)
        out_pos += len(new_frag)
        last = end
    chunks.append(text[last:])
    new_text = "".join(chunks)

    new_entities: list[Entity] = []
    for i, ent in enumerate(entities):
        if i in replaced_indices:
            new_entities.append(replaced_indices[i])
        else:
            new_entities.append(
                ent.model_copy(
                    update={
                        "start": _map_position(ent.start, slim_edits),
                        "end": _map_position(ent.end, slim_edits),
                    }
                )
            )
    return new_text, new_entities


def generalize_birthdate(
    text: str,
    entities: list[Entity],
    config: GeneralizationConfig | None = None,
) -> tuple[str, list[Entity]]:
    """BR-B01: BIRTHDATE → enkel geboortejaar; entity-type → BIRTH_YEAR."""
    cfg = config or GeneralizationConfig()
    if not cfg.birthdate:
        return text, _copy_entities(entities)

    plans: dict[int, tuple[int, int, str, EntityType]] = {}
    for i, ent in enumerate(entities):
        if ent.entity_type is not EntityType.BIRTHDATE:
            continue
        year = _birthyear_from_entity(ent)
        if year is None:
            continue
        repl = str(year)
        plans[i] = (ent.start, ent.end, repl, EntityType.BIRTH_YEAR)
    return _apply_replacements(text, entities, plans)


def generalize_postcode(
    text: str,
    entities: list[Entity],
    config: GeneralizationConfig | None = None,
) -> tuple[str, list[Entity]]:
    """BR-B02: POSTCODE_PC6 → POSTCODE_PC2 (eerste twee cijfers)."""
    cfg = config or GeneralizationConfig()
    if not cfg.postcode:
        return text, _copy_entities(entities)

    plans: dict[int, tuple[int, int, str, EntityType]] = {}
    for i, ent in enumerate(entities):
        if ent.entity_type is not EntityType.POSTCODE_PC6:
            continue
        digits = re.sub(r"\D", "", ent.original)
        if len(digits) < 2:
            logger.warning("PC6 zonder twee cijfers: %r", ent.original)
            continue
        pc2 = digits[:2]
        plans[i] = (ent.start, ent.end, pc2, EntityType.POSTCODE_PC2)
    return _apply_replacements(text, entities, plans)


def generalize_age(
    text: str,
    entities: list[Entity],
    config: GeneralizationConfig | None = None,
) -> tuple[str, list[Entity]]:
    """BR-B03: leeftijd ≥ 90 → '90+ jaar' / '90+ jarige'."""
    cfg = config or GeneralizationConfig()
    if not cfg.age:
        return text, _copy_entities(entities)

    plans: dict[int, tuple[int, int, str, EntityType]] = {}
    for i, ent in enumerate(entities):
        if ent.entity_type is not EntityType.AGE:
            continue
        m_num = re.search(r"\d+", ent.original)
        if m_num is None:
            continue
        age_val = int(m_num.group())
        if age_val < 90:
            continue
        repl = "90+ jarige" if re.search(r"jarige", ent.original, re.IGNORECASE) else "90+ jaar"
        plans[i] = (ent.start, ent.end, repl, EntityType.AGE)
    return _apply_replacements(text, entities, plans)


def generalize_treatment_dates(
    text: str,
    entities: list[Entity],
    config: GeneralizationConfig | None = None,
) -> tuple[str, list[Entity]]:
    """BR-B04: ADMISSION/DISCHARGE/EXAM-datum → YYYY-MM; ' op <datum>' → ' in YYYY-MM'."""
    cfg = config or GeneralizationConfig()
    if not cfg.treatment_dates:
        return text, _copy_entities(entities)

    types = frozenset(
        {
            EntityType.ADMISSION_DATE,
            EntityType.DISCHARGE_DATE,
            EntityType.EXAM_DATE,
        }
    )
    plans: dict[int, tuple[int, int, str, EntityType]] = {}
    for i, ent in enumerate(entities):
        if ent.entity_type not in types:
            continue
        ym = _year_month_from_entity(ent)
        if ym is None:
            continue
        s, e, frag = _treatment_span_and_text(text, ent, ym)
        plans[i] = (s, e, frag, ent.entity_type)
    return _apply_replacements(text, entities, plans)


def flag_rare_diagnoses(
    text: str,
    entities: list[Entity],
    config: GeneralizationConfig | None = None,
) -> tuple[str, list[Entity]]:
    """BR-B05: zeldzame ICD-10 → `rare_icd_review` op entity (geen tekstwijziging in v0.3)."""
    cfg = config or GeneralizationConfig()
    if not cfg.flag_rare_icd:
        return text, _copy_entities(entities)

    out: list[Entity] = []
    for ent in entities:
        if ent.entity_type is not EntityType.ICD10_CODE:
            out.append(ent.model_copy())
            continue
        code = _normalize_icd_code(ent.original)
        if code in RARE_ICD10_CODES:
            out.append(ent.model_copy(update={"rare_icd_review": True}))
        else:
            out.append(ent.model_copy())
    return text, out


def apply_generalizations(
    text: str,
    entities: list[Entity],
    config: GeneralizationConfig | None = None,
) -> tuple[str, list[Entity]]:
    """Alias voor `generalize_all` (zelfde keten)."""

    return generalize_all(text, entities, config)


def generalize_all(
    text: str,
    entities: list[Entity],
    config: GeneralizationConfig | None = None,
) -> tuple[str, list[Entity]]:
    """Voer BR-B01 t/m B05 sequentieel uit; `config=None` laadt uit de content-database."""
    cfg = config if config is not None else GeneralizationConfig.from_db()
    text, entities = generalize_birthdate(text, entities, cfg)
    text, entities = generalize_postcode(text, entities, cfg)
    text, entities = generalize_age(text, entities, cfg)
    text, entities = generalize_treatment_dates(text, entities, cfg)
    text, entities = flag_rare_diagnoses(text, entities, cfg)
    return text, entities
