"""Utility functions for discovering Ukrainian company websites by EDRPOU code.

The module exposes two high-level helpers:

``search_candidate_domains`` searches the public web (DuckDuckGo) for domains
that likely belong to the requested company.  ``parse_company_contacts``
combines the discovery step with a lightweight contact scraper that extracts
emails, phone numbers and social links from the landing pages of the candidate
domains.

The implementation is intentionally defensive: all network calls rely on short
timeouts, a desktop User-Agent header and optional retries.  HTML parsing uses
BeautifulSoup while the contact extraction relies on simple regular expressions
that cover the majority of Ukrainian formatting conventions.

The functions return rich Python dictionaries so they can be consumed either in
stand-alone scripts or FastAPI endpoints.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import logging
import re
from typing import Dict, Iterable, List, Optional, Sequence
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from duckduckgo_search import DDGS


logger = logging.getLogger(__name__)


DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
)


STOP_WORDS = {
    "тов",
    "фоп",
    "приватне",
    "акціонерне",
    "спільне",
    "підприємство",
    "виробниче",
    "виробничо",
    "торгівельне",
    "державне",
    "українське",
    "товариство",
    "з",
    "обмеженою",
    "відповідальністю",
}


SOCIAL_DOMAINS = (
    "facebook.com",
    "instagram.com",
    "linkedin.com",
    "twitter.com",
    "x.com",
    "youtube.com",
    "t.me",
    "telegram.me",
    "tiktok.com",
    "vk.com",
)


PHONE_REGEX = re.compile(
    r"(?:\+?380|0)(?:[\s\-()]*\d){9,10}",
    re.MULTILINE,
)

EMAIL_REGEX = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")

URL_REGEX = re.compile(
    r"https?://[\w.-]+(?:/[\w./%#?-]*)?",
    re.IGNORECASE,
)


@dataclass
class ContactInfo:
    """Structured contact information extracted from a single page."""

    url: str
    emails: List[str]
    phones: List[str]
    other_urls: List[str]
    social_profiles: List[str]


def _tokenise_company_name(name: str) -> Sequence[str]:
    tokens = [
        token
        for token in re.findall(r"[\w']+", name.lower())
        if token not in STOP_WORDS and len(token) > 2
    ]
    return tokens


def _domain_from_url(url: str) -> str:
    parsed = urlparse(url)
    hostname = parsed.hostname or ""
    if hostname.startswith("www."):
        hostname = hostname[4:]
    return hostname


def _score_domain(domain: str, tokens: Sequence[str]) -> int:
    if not domain:
        return 0
    domain_parts = re.split(r"[.\-]", domain.lower())
    counter = Counter(domain_parts)
    score = 0
    for token in tokens:
        if token in counter:
            score += counter[token] * 2
        elif token in domain:
            score += 1
    return score


def _iter_duckduckgo_results(query: str, max_results: int) -> Iterable[Dict[str, str]]:
    with DDGS() as ddgs:
        for result in ddgs.text(query, region="ua-uk", safesearch="moderate", max_results=max_results):
            yield result


def search_candidate_domains(
    company_name: str,
    edrpou: Optional[str] = None,
    address: Optional[str] = None,
    *,
    max_candidates: int = 5,
    max_results_per_query: int = 20,
) -> List[str]:
    """Return a ranked list of candidate domains that may belong to the company."""

    if not company_name:
        return []

    tokens = _tokenise_company_name(company_name)

    queries = [f'"{company_name}" офіційний сайт']
    if edrpou:
        queries.append(f'"{company_name}" "{edrpou}" сайт')
    if address:
        queries.append(f'"{company_name}" "{address}" сайт')

    scored_domains: Dict[str, int] = {}

    for query in queries:
        try:
            for result in _iter_duckduckgo_results(query, max_results_per_query):
                url = result.get("href") or result.get("url")
                if not url:
                    continue
                domain = _domain_from_url(url)
                if not domain:
                    continue
                score = _score_domain(domain, tokens)
                if edrpou and edrpou in (result.get("title", "") + result.get("body", "")):
                    score += 3
                if address and address.lower() in (result.get("body", "").lower()):
                    score += 1
                if domain in scored_domains:
                    scored_domains[domain] = max(scored_domains[domain], score)
                else:
                    scored_domains[domain] = score
        except Exception as exc:  # pragma: no cover - network failures are non-deterministic
            logger.warning("DuckDuckGo search failed for query %s: %s", query, exc)

    ranked = sorted(scored_domains.items(), key=lambda item: item[1], reverse=True)
    return [domain for domain, _ in ranked[:max_candidates]]


def _fetch_html(url: str, timeout: float = 10.0) -> Optional[str]:
    try:
        response = requests.get(
            url,
            headers={"User-Agent": DEFAULT_USER_AGENT},
            timeout=timeout,
            allow_redirects=True,
        )
        if response.ok:
            response.encoding = response.encoding or "utf-8"
            return response.text
        logger.warning("Failed to fetch %s: HTTP %s", url, response.status_code)
    except requests.RequestException as exc:  # pragma: no cover - depends on network
        logger.warning("Network error while fetching %s: %s", url, exc)
    return None


def _absolute_links(base_url: str, soup: BeautifulSoup) -> Iterable[str]:
    for tag in soup.find_all("a", href=True):
        href = tag["href"].strip()
        if href.startswith("javascript:") or href.startswith("mailto:"):
            continue
        yield urljoin(base_url, href)


def _extract_contact_info(url: str, html: str) -> ContactInfo:
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(" ")

    emails = sorted(set(match.group(0).lower() for match in EMAIL_REGEX.finditer(text)))
    phones = sorted(set(_normalise_phone(match.group(0)) for match in PHONE_REGEX.finditer(text)))

    urls = {match.group(0) for match in URL_REGEX.finditer(html)}
    for link in _absolute_links(url, soup):
        urls.add(link)

    social_profiles = sorted({link for link in urls if _is_social_url(link)})
    other_urls = sorted({link for link in urls if link not in social_profiles})

    return ContactInfo(
        url=url,
        emails=emails,
        phones=phones,
        other_urls=other_urls,
        social_profiles=social_profiles,
    )


def _normalise_phone(raw_phone: str) -> str:
    cleaned = re.sub(r"[^\d+]", "", raw_phone)
    digits = re.sub(r"\D", "", cleaned)

    if digits.startswith("380") and len(digits) == 12:
        return "+" + digits
    if digits.startswith("0") and len(digits) == 10:
        return "+38" + digits
    if digits.startswith("80") and len(digits) == 11:
        return "+3" + digits

    if cleaned.startswith("+"):
        return cleaned
    return "+" + digits if digits else cleaned


def _is_social_url(url: str) -> bool:
    domain = _domain_from_url(url)
    return any(domain.endswith(social) for social in SOCIAL_DOMAINS)


def parse_company_contacts(
    company_name: str,
    edrpou: Optional[str] = None,
    address: Optional[str] = None,
    *,
    max_candidates: int = 3,
) -> Dict[str, List[ContactInfo]]:
    """Discover candidate domains and fetch their contact information."""

    candidates = search_candidate_domains(
        company_name,
        edrpou,
        address,
        max_candidates=max_candidates,
    )

    contacts: Dict[str, List[ContactInfo]] = {}
    for domain in candidates:
        url = f"https://{domain}"
        html = _fetch_html(url)
        if not html:
            continue
        contact_info = _extract_contact_info(url, html)
        contacts.setdefault(domain, []).append(contact_info)
    return contacts


__all__ = [
    "ContactInfo",
    "parse_company_contacts",
    "search_candidate_domains",
]

