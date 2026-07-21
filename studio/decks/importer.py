"""Moteur universel d'importation de decklists -> format natif OPTCGSim.

Trois portes d'entrée, une seule sortie normalisée (`Decklist`) :

  parse_text(txt)        tous les formats texte communautaires (voir _LINE_PATTERNS)
  from_clipboard()       lit le presse-papiers (pbpaste / xclip / PowerShell — sans dépendance)
  from_url(url)          télécharge la page et en extrait la liste (générique best-effort)

Le format pivot est le format NATIF du sim (observé dans ses .txt réels) :

    1xPRB01-001        <- 1re entrée = leader
    4xOP09-002
    ...

Règles OPTCG validées à l'import : exactement 1 leader + 50 cartes, ≤ 4 exemplaires par id.

Note de robustesse : les sites majeurs (NakamaDecks, EgmanEvents, LimitlessTCG) offrent tous
un bouton « Export OPTCGSim » qui produit exactement le format natif — le duo export-bouton →
`from_clipboard()` est le chemin GARANTI. `from_url()` tente l'extraction générique (paires
quantité×id dans le HTML) et échoue proprement avec ce conseil si la page ne se laisse pas
lire. Pas de scraper par site : leurs DOM changent, le format natif non.
"""

from __future__ import annotations

import re
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from ..nettls import CERT_FIX_HINT, is_cert_error, ssl_context

# Même gabarit d'id que le reste de l'écosystème (validé sur des années de logs).
CARD_ID = r"(?:P-[A-Z0-9]+|[A-Z]{2,4}\d{2}-\d{3})"
_ID_RE = re.compile(rf"^{CARD_ID}$")

DECK_SIZE = 50
MAX_COPIES = 4


class ImportError_(Exception):
    """Erreur d'import (nom évitant l'ombrage du builtin ImportError)."""


@dataclass
class Decklist:
    leader: str
    cards: dict[str, int] = field(default_factory=dict)   # hors leader
    name: str | None = None
    source: str | None = None

    @property
    def total(self) -> int:
        return sum(self.cards.values())

    def validate(self) -> None:
        if not _ID_RE.match(self.leader):
            raise ImportError_(f"Leader invalide : {self.leader!r}")
        if self.total != DECK_SIZE:
            raise ImportError_(
                f"Deck de {self.total} cartes (attendu {DECK_SIZE}) — import incomplet ?")
        for cid, qty in self.cards.items():
            if not _ID_RE.match(cid):
                raise ImportError_(f"Id de carte invalide : {cid!r}")
            if not 1 <= qty <= MAX_COPIES:
                raise ImportError_(f"{cid} : {qty} exemplaires (max {MAX_COPIES})")

    def to_native_text(self) -> str:
        """Format natif du sim : leader en 1re ligne, puis cartes triées par id."""
        lines = [f"1x{self.leader}"]
        lines += [f"{qty}x{cid}" for cid, qty in sorted(self.cards.items())]
        return "\n".join(lines) + "\n"

    def save_to_sim(self, name: str, persistent_dir: Path) -> Path:
        """Écrit la decklist là où le sim lit ses decks (racine du dossier persistant).

        Refuse d'écraser un deck existant sauf s'il porte déjà notre contenu (idempotent).
        """
        path = deck_txt_path(name, persistent_dir)
        text = self.to_native_text()
        if path.exists() and path.read_text(errors="ignore") != text:
            raise ImportError_(
                f"{path.name} existe déjà avec un contenu différent — choisir un autre nom")
        path.write_text(text)
        return path


def deck_txt_path(name: str, persistent_dir: Path) -> Path:
    """Chemin `.txt` où le sim lit/écrit un deck de ce nom (racine du dossier persistant) —
    même assainissement que `save_to_sim`, factorisé pour que la suppression (studio) vise
    exactement le même fichier que l'écriture."""
    safe = re.sub(r"[^\w \-']", "_", name).strip() or "Imported"
    return Path(persistent_dir) / f"{safe}.txt"


def scan_persistent_decks(persistent_dir: Path) -> list[Decklist]:
    """Scanne les `.txt` à la RACINE du dossier persistant (non récursif — ignore les caches
    versionnés `<version>/Cards/`) et les parse en `Decklist` (`source="sim"`).

    Un `.txt` qui n'est pas une decklist valide (log, fichier tiers) est ignoré silencieusement
    plutôt que de faire échouer tout le scan — même principe que les entrées en échec de
    `deckpack.resolve()`."""
    persistent_dir = Path(persistent_dir)
    if not persistent_dir.exists():
        return []
    out = []
    for p in sorted(persistent_dir.glob("*.txt")):
        try:
            out.append(parse_text(p.read_text(errors="ignore"), name=p.stem, source="sim"))
        except ImportError_:
            continue
    return out


def sync_with_store(persistent_dir: Path, store) -> dict:
    """Concilie les `.txt` du dossier persistant (`scan_persistent_decks`) avec les decks en
    base (`store`, protocole SyncStore) — TOUJOURS additif, jamais de suppression :

      - absent en base                            -> nouveau (persisté, `source="sim"`)
      - présent, contenu natif différent           -> mis à jour (`leader`/`cards` seulement ;
                                                       `tags`/`id`/`profile_id` préservés)
      - en base avec `source="sim"`, absent du scan -> orphelin (signalé seulement)

    Factorisé ici (plutôt que dupliqué dans l'API et la CLI) car c'est plus qu'un simple
    appel de persistance : c'est un algorithme de diff à tenir cohérent aux deux endroits.
    Renvoie `{"new": [...], "updated": [...], "orphaned": [...]}` (noms de decks)."""
    found = {d.name: d for d in scan_persistent_decks(persistent_dir)}
    rows = store.list("decks")
    by_name = {r["name"]: r for r in rows}
    profiles = store.list("profiles")
    prof = profiles[0] if profiles else store.put("profiles", {"name": "default", "prefs": {}})
    new, updated = [], []
    for name, deck in found.items():
        row = by_name.get(name)
        if row is None:
            store.put("decks", {"profile_id": prof["id"], "name": name,
                                "leader": deck.leader, "cards": deck.cards,
                                "tags": [], "source": deck.source})
            new.append(name)
        else:
            expected = Decklist(leader=row["leader"], cards=row["cards"]).to_native_text()
            if deck.to_native_text() != expected:
                store.put("decks", {**row, "leader": deck.leader, "cards": deck.cards})
                updated.append(name)
    orphaned = [r["name"] for r in rows
               if r["name"] not in found and r.get("source") == "sim"]
    return {"new": new, "updated": updated, "orphaned": orphaned}


# --------------------------------------------------------------------------- parsing texte
# Chaque motif capture (qty, id) ou (id, qty). Ordre = du plus strict au plus permissif.
_LINE_PATTERNS: list[tuple[re.Pattern, tuple[int, int]]] = [
    # 4xOP01-001 / 4x OP01-001 / 4 x OP01-001   (natif + variantes)
    (re.compile(rf"^(\d+)\s*[xX]\s*({CARD_ID})\b"), (1, 2)),
    # 4 OP01-001
    (re.compile(rf"^(\d+)\s+({CARD_ID})\b"), (1, 2)),
    # OP01-001 x4 / OP01-001 ×4
    (re.compile(rf"^({CARD_ID})\s*[x×X]\s*(\d+)\b"), (2, 1)),
    # 4 Monkey D. Luffy (OP01-001)   (format « count name (ID) »)
    (re.compile(rf"^(\d+)\s*[xX]?\s+.*?\(({CARD_ID})\)"), (1, 2)),
]

_SECTION_LEADER = re.compile(r"^\s*leaders?\s*:?\s*$", re.IGNORECASE)


def parse_text(text: str, name: str | None = None,
               source: str | None = None) -> Decklist:
    """Parse un texte de decklist multi-formats -> Decklist validée.

    Leader : section « Leader » explicite si présente, sinon la 1re entrée à quantité 1
    (convention du format natif : le leader ouvre la liste).
    """
    entries: list[tuple[str, int]] = []           # (id, qty) dans l'ordre du texte
    leader_from_section: str | None = None
    in_leader_section = False
    for raw in text.splitlines():
        line = raw.strip().lstrip("-•*").strip()
        if not line or line.startswith(("#", "//")):
            continue
        if _SECTION_LEADER.match(line):
            in_leader_section = True
            continue
        for pat, (qi, ii) in _LINE_PATTERNS:
            m = pat.match(line)
            if m:
                qty, cid = int(m.group(qi)), m.group(ii)
                if in_leader_section and leader_from_section is None:
                    leader_from_section = cid
                else:
                    entries.append((cid, qty))
                in_leader_section = False
                break
        else:
            # ligne sans quantité mais id nu dans une section Leader ("Leader: OP01-001")
            m = re.search(rf"\b({CARD_ID})\b", line)
            if m and in_leader_section and leader_from_section is None:
                leader_from_section = m.group(1)
                in_leader_section = False
    if not entries and not leader_from_section:
        raise ImportError_("Aucune entrée de decklist reconnue dans le texte")

    if leader_from_section:
        leader = leader_from_section
    else:
        # convention native : 1re entrée à qty=1 = leader
        first_singles = [cid for cid, q in entries[:1] if q == 1]
        if not first_singles:
            raise ImportError_(
                "Leader indéterminable (pas de section Leader, la liste ne commence pas "
                "par une entrée 1x) — préciser le leader ou utiliser l'export OPTCGSim du site")
        leader = first_singles[0]
        entries = entries[1:]

    cards: dict[str, int] = {}
    for cid, qty in entries:
        cards[cid] = cards.get(cid, 0) + qty
    deck = Decklist(leader=leader, cards=cards, name=name, source=source)
    deck.validate()
    return deck


# --------------------------------------------------------------------------- presse-papiers
def read_clipboard() -> str:
    """Contenu texte du presse-papiers, sans dépendance externe (outils OS natifs)."""
    cmds = {
        "darwin": ["pbpaste"],
        "linux": ["xclip", "-selection", "clipboard", "-o"],
        "win32": ["powershell", "-NoProfile", "-Command", "Get-Clipboard"],
    }
    cmd = cmds.get(sys.platform.rstrip("0123456789") if sys.platform.startswith("linux")
                   else sys.platform)
    if cmd is None:
        raise ImportError_(f"Presse-papiers non géré sur {sys.platform}")
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
    except FileNotFoundError as e:
        raise ImportError_(f"Outil presse-papiers absent : {cmd[0]}") from e
    return out.stdout


def from_clipboard(name: str | None = None) -> Decklist:
    return parse_text(read_clipboard(), name=name, source="clipboard")


# --------------------------------------------------------------------------- URL (générique)
_UA = ("Mozilla/5.0 (compatible; optcgsim-studio/0.1; "
       "+https://github.com/hquezser)")

# Paires quantité×id trouvables dans le HTML même sans connaître le DOM du site :
#   "4x</span> ... OP01-001", data-qty="4" data-id="OP01-001", "OP01-001 ×4", etc.
_HTML_PAIRS = [
    re.compile(rf"(\d)\s*[x×]\s*(?:<[^>]+>\s*)*({CARD_ID})"),
    re.compile(rf"({CARD_ID})(?:\s*<[^>]+>)*\s*[x×]\s*(\d)"),
]


def fetch_url(url: str, timeout: float = 10.0) -> str:
    if not url.lower().startswith(("http://", "https://")):
        raise ImportError_(f"URL invalide : {url}")
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    try:
        with urllib.request.urlopen(  # noqa: S310 (schéma vérifié)
                req, timeout=timeout, context=ssl_context()) as resp:
            return resp.read().decode("utf-8", errors="ignore")
    except urllib.error.URLError as e:
        # voir packlib._download : urlopen() enveloppe le handshake SSL dans un URLError.
        if is_cert_error(e):
            raise ImportError_(CERT_FIX_HINT) from e
        raise


def parse_html(html: str, name: str | None = None,
               source: str | None = None) -> Decklist:
    """Extraction générique d'une decklist depuis une page HTML.

    1. Si la page embarque un bloc au format natif (textarea d'export), parse_text le voit.
    2. Sinon, collecte des paires quantité×id ; valide si le total tombe juste (1 + 50).
    """
    # 1) bloc natif embarqué ? (textarea/pre d'export)
    naked = re.sub(r"<[^>]+>", "\n", html)
    try:
        return parse_text(naked, name=name, source=source)
    except ImportError_:
        pass
    # 2) paires génériques dans le HTML brut
    pairs: dict[str, int] = {}
    order: list[str] = []
    for pat in _HTML_PAIRS:
        for m in pat.finditer(html):
            g1, g2 = m.group(1), m.group(2)
            qty, cid = (int(g1), g2) if g1.isdigit() else (int(g2), g1)
            if cid not in pairs:
                order.append(cid)
            pairs[cid] = max(pairs.get(cid, 0), qty)   # dédoublonne les répétitions du DOM
        if pairs:
            break
    if not pairs:
        raise ImportError_(
            "Aucune decklist extractible de cette page — utiliser le bouton "
            "« Export OPTCGSim » du site puis `studio decks import --clipboard`")
    leader = next((cid for cid in order if pairs[cid] == 1), order[0])
    cards = {cid: q for cid, q in pairs.items() if cid != leader}
    deck = Decklist(leader=leader, cards=cards, name=name, source=source)
    deck.validate()
    return deck


def from_url(url: str, name: str | None = None) -> Decklist:
    return parse_html(fetch_url(url), name=name, source=url)
