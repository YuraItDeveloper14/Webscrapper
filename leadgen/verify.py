"""Find out whether a business really has a website.

OpenStreetMap leaves the website tag empty for most businesses, so "no site
listed" is not the same as "no site". Calling a clinic and telling them they
have no website when they do is the worst possible opening, so before a lead is
presented as a prospect we try to prove the opposite:

    1. build the domains such a business would plausibly own, and
    2. fetch them, treating the business's own phone number on the page as proof.

A domain that answers and spells out the full business name counts as "likely";
only a phone match counts as "confirmed". Anything weaker is ignored — a false
"they have a site" hides a real customer just as badly.
"""
from __future__ import annotations

import re
import asyncio
import httpx

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36")

# Two spellings per letter where Ukrainian/Russian names are romanised both ways
# (Авіцена is "avicena" in the wild, not the official "avitsena").
_TR = {
    "а": ["a"], "б": ["b"], "в": ["v"], "г": ["h", "g"], "ґ": ["g"], "д": ["d"],
    "е": ["e"], "ё": ["e"], "є": ["ie", "ye", "e"], "ж": ["zh", "j"], "з": ["z"],
    "и": ["y", "i"], "і": ["i"], "ї": ["i", "yi"], "й": ["i", "y"], "к": ["k", "c"],
    "л": ["l"], "м": ["m"], "н": ["n"], "о": ["o"], "п": ["p"], "р": ["r"],
    "с": ["s"], "т": ["t"], "у": ["u"], "ф": ["f"], "х": ["kh", "h", "x"],
    "ц": ["ts", "c"], "ч": ["ch"], "ш": ["sh"], "щ": ["shch", "sch"], "ъ": [""],
    "ы": ["y"], "ь": [""], "э": ["e"], "ю": ["iu", "yu", "u"], "я": ["ia", "ya", "a"],
}
# Words that describe the trade rather than name the business.
STOP = {"стоматологія", "стоматология", "стоматологічна", "стоматологический",
        "клініка", "клиника", "кабінет", "кабинет", "центр", "медичний", "мед",
        "clinic", "dental", "dentistry", "studio", "group", "the", "and", "та", "и",
        "ооо", "тов", "фоп", "приватна", "сімейна"}
CITY_TLD = {"київ": "kiev.ua", "киев": "kiev.ua", "kyiv": "kiev.ua",
            "івано-франківськ": "if.ua", "львів": "lviv.ua", "lviv": "lviv.ua",
            "одеса": "odessa.ua", "харків": "kharkov.ua", "дніпро": "dp.ua",
            "тернопіль": "te.ua", "ужгород": "uz.ua", "чернівці": "cv.ua"}
MAX_DOMAINS = 14


def _variants(word: str) -> list[str]:
    """Romanised spellings of one word (kept to a few to stay cheap)."""
    outs = [""]
    for ch in word.lower():
        opts = _TR.get(ch, [ch if ch.isalnum() else ""])
        outs = [o + v for o in outs for v in opts][:6]
    return list(dict.fromkeys(re.sub(r"[^a-z0-9]", "", o) for o in outs if o))


def candidate_domains(name: str, city: str = "") -> list[str]:
    words = [w for w in re.sub(r"[^\w\s\-]", " ", (name or "").lower()).split()
             if w and w not in STOP]
    if not words:
        return []
    per_word = [_variants(w) for w in words]
    per_word = [v for v in per_word if v]
    if not per_word:
        return []
    per_word = [["dr"] if v and v[0] in ("doktor", "doctor") else v for v in per_word]

    slugs: list[str] = []
    if len(per_word) == 1:
        slugs += [w for w in per_word[0] if len(w) >= 4]
    else:
        for a in per_word[0][:3]:
            for b in per_word[1][:3]:
                slugs += [a + b, f"{a}-{b}"]
        # the distinctive word on its own, when it is long enough to be a name
        for w in per_word[-1][:2] + per_word[0][:2]:
            if len(w) >= 6:
                slugs.append(w)
    slugs = [s for s in dict.fromkeys(slugs) if 4 <= len(s) <= 26]

    tlds = ["com.ua", "ua", "com"]
    ct = CITY_TLD.get((city or "").strip().lower())
    if ct:
        tlds.insert(0, ct)
    return [f"{s}.{t}" for s in slugs for t in tlds][:MAX_DOMAINS]


def phone_keys(phone: str | None) -> set[str]:
    """Last 7 digits of every number listed — format-independent fingerprints."""
    keys = set()
    for part in re.split(r"[;,/]", phone or ""):
        d = re.sub(r"\D", "", part)
        if len(d) >= 9:
            keys.add(d[-7:])
    return keys


def _full_name_slug(domain: str, name: str) -> bool:
    """True when the domain spells out every meaningful word of the name."""
    words = [w for w in re.sub(r"[^\w\s\-]", " ", (name or "").lower()).split()
             if w and w not in STOP]
    if len(words) < 2:
        return False
    host = domain.split(".")[0].replace("-", "")
    return all(any(v and v in host for v in _variants(w)) for w in words)


async def find_site(client: httpx.AsyncClient, name: str, city: str,
                    phone: str | None) -> str:
    """Return the business's own website, or "" when it cannot be proven.

    Only a page carrying the business's own phone number counts. Guessing from
    the name alone was tried and rejected: it matched a US clinic called
    "albusdens.com" to a practice in Ivano-Frankivsk, and a wrong "they already
    have a site" quietly buries a real customer.
    """
    keys = phone_keys(phone)
    if not keys:
        return ""
    for domain in candidate_domains(name, city):
        for scheme in ("https://", "http://"):
            try:
                r = await client.get(scheme + domain, timeout=12, follow_redirects=True,
                                     headers={"User-Agent": UA})
            except Exception:
                continue
            if r.status_code == 200 and any(k in re.sub(r"\D", "", r.text) for k in keys):
                return scheme + domain
            break            # the domain answered; no need to try the other scheme
    return ""


async def verify_leads(leads: list[dict], concurrency: int = 6) -> int:
    """Fill in `website` for leads that have none. Returns how many were found."""
    sem = asyncio.Semaphore(concurrency)
    found = 0

    async with httpx.AsyncClient() as client:
        async def one(lead):
            nonlocal found
            if (lead.get("website") or "").strip():
                return
            async with sem:
                url = await find_site(client, lead.get("name", ""),
                                      lead.get("city") or lead.get("region") or "",
                                      lead.get("phone"))
            if url:
                lead["website"] = url
                found += 1

        await asyncio.gather(*(one(l) for l in leads))
    return found
