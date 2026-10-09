from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse


@dataclass(frozen=True)
class SourceDefinition:
    key: str
    label: str
    kind: str
    domains: tuple[str, ...]
    note: str = ""
    automated_page_read: bool = True
    page_read_block_reason: str = ""
    policy_observed_at: str = ""


VIN_SOURCES: tuple[SourceDefinition, ...] = (
    SourceDefinition(
        key="nhtsa_vpic",
        label="NHTSA vPIC",
        kind="vin",
        domains=("vpic.nhtsa.dot.gov",),
        note="Primary VIN decoder.",
    ),
)

PARTS_CATALOG_SOURCES: tuple[SourceDefinition, ...] = (
    SourceDefinition(
        key="partsouq",
        label="PartSouq",
        kind="catalog",
        domains=("partsouq.com",),
        note="OEM catalog and part detail pages.",
    ),
    SourceDefinition(
        key="amayama",
        label="Amayama",
        kind="catalog",
        domains=("amayama.com",),
        note="OEM catalog and cross-reference source.",
    ),
    SourceDefinition(
        key="megazip",
        label="MegaZip",
        kind="catalog",
        domains=("megazip.net",),
        note="OEM catalog source.",
    ),
)

PARTS_PRICE_SOURCES: tuple[SourceDefinition, ...] = (
    SourceDefinition(
        key="emex",
        label="Emex",
        kind="price",
        domains=("emex.ru",),
        note="Market price source.",
    ),
    SourceDefinition(
        key="autopiter",
        label="Autopiter",
        kind="price",
        domains=("autopiter.ru",),
        note="Market price source.",
    ),
    SourceDefinition(
        key="exist",
        label="Exist",
        kind="price",
        domains=("exist.ru",),
        note="Market price source.",
    ),
    SourceDefinition(
        key="zzap",
        label="ZZAP",
        kind="price",
        domains=("zzap.ru",),
        note="Market price source.",
    ),
)

# E8 is deliberately narrower than the legacy catalog and price source lists.
# Its public-evidence contract must stay aligned with the canonical Manager IDs.
E8_PART_EVIDENCE_SOURCES: tuple[SourceDefinition, ...] = (
    SourceDefinition(
        key="elcats_catalog",
        label="Elcats",
        kind="oem_catalog",
        domains=("elcats.ru",),
        note="Public HTML references; observed robots policy blocks automated catalog-page reads. Not an official API.",
        automated_page_read=False,
        page_read_block_reason="robots_disallowed",
        policy_observed_at="2026-10-09",
    ),
    SourceDefinition(
        key="japancats_catalog",
        label="Japancats",
        kind="oem_catalog",
        domains=("japancats.ru",),
        note="Distinct public HTML lineage; observed robots policy blocks automated catalog-page reads.",
        automated_page_read=False,
        page_read_block_reason="robots_disallowed",
        policy_observed_at="2026-10-09",
    ),
    SourceDefinition(
        key="exist_ssangyong_catalog",
        label="Exist Ssangyong catalog",
        kind="oem_catalog",
        domains=("ssangyong.exist.ru",),
        note="Separate public catalog lineage; catalog observations do not establish exact VIN fitment.",
    ),
    SourceDefinition(
        key="partsouq_catalog",
        label="PartSouq",
        kind="oem_catalog",
        domains=("partsouq.com",),
        note="Public OEM catalog evidence.",
    ),
    SourceDefinition(
        key="amayama_catalog",
        label="Amayama",
        kind="oem_catalog",
        domains=("amayama.com",),
        note="Public OEM catalog evidence.",
    ),
    SourceDefinition(
        key="emex_public",
        label="Emex",
        kind="price_catalog",
        domains=("emex.ru",),
        note="Public price-catalog evidence.",
    ),
    SourceDefinition(
        key="exist",
        label="Exist",
        kind="price_catalog",
        domains=("exist.ru",),
        note="Public price-catalog evidence.",
    ),
)

DIAGNOSTIC_SOURCES: tuple[SourceDefinition, ...] = (
    SourceDefinition(
        key="obd_codes",
        label="OBD-Codes",
        kind="dtc",
        domains=("obd-codes.com",),
        note="Trouble code reference.",
    ),
    SourceDefinition(
        key="dtcdecode",
        label="DTCDecode",
        kind="dtc",
        domains=("dtcdecode.com",),
        note="Trouble code lookup reference.",
    ),
    SourceDefinition(
        key="carcarekiosk",
        label="CarCareKiosk",
        kind="fault",
        domains=("carcarekiosk.com",),
        note="Symptom and repair info.",
    ),
)

GENERIC_WEB_SOURCES: tuple[SourceDefinition, ...] = (
    SourceDefinition(
        key="multi_search",
        label="SearXNG/Marginalia/DuckDuckGo/Tavily/Brave/Google CSE",
        kind="search",
        domains=(
            "searxng.org",
            "marginalia-search.com",
            "duckduckgo.com",
            "tavily.com",
            "search.brave.com",
            "googleapis.com",
            "crawl4ai.com",
        ),
        note="Configured public search providers for discovery; page excerpts use validated, bounded HTTP requests.",
    ),
)


def trusted_domains(*, kind: str) -> list[str]:
    registries = {
        "vin": VIN_SOURCES,
        "catalog": PARTS_CATALOG_SOURCES,
        "price": PARTS_PRICE_SOURCES,
        "dtc": DIAGNOSTIC_SOURCES,
        "fault": DIAGNOSTIC_SOURCES,
        "search": GENERIC_WEB_SOURCES,
    }
    return [domain for item in registries.get(kind, ()) for domain in item.domains]


def public_part_evidence_domains() -> list[str]:
    """Return the fixed public allowlist for the E8 part-evidence gateway."""

    return [domain for item in E8_PART_EVIDENCE_SOURCES for domain in item.domains]


def public_catalog_page_read_policy(url: str) -> dict[str, str] | None:
    """Return a captured source-specific restriction, without DNS or HTTP.

    Only the plain robots file remains readable for a future explicit policy
    review. The captured restriction is not a claim about future site policy.
    """
    try:
        parsed = urlparse(url)
        hostname = str(parsed.hostname or "").casefold().rstrip(".")
    except ValueError:
        return None
    if parsed.path == "/robots.txt" and not parsed.params and not parsed.query:
        return None
    for source in E8_PART_EVIDENCE_SOURCES:
        if source.automated_page_read:
            continue
        if any(hostname == domain or hostname.endswith(f".{domain}") for domain in source.domains):
            return {
                "source_id": source.key,
                "reason": source.page_read_block_reason,
                "observed_at": source.policy_observed_at,
            }
    return None


def describe_sources() -> str:
    lines: list[str] = []
    for group_name, items in (
        ("VIN", VIN_SOURCES),
        ("CATALOG", PARTS_CATALOG_SOURCES),
        ("PRICE", PARTS_PRICE_SOURCES),
    ):
        labels = ", ".join(f"{item.label} ({'/'.join(item.domains)})" for item in items)
        lines.append(f"{group_name}: {labels}")
    return "\n".join(lines)
