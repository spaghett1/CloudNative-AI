"""Der Zustand einer Unterhaltung als Datenobjekt, mit Checkpoint auf der Platte.

In agent.py lebt der Zustand nur in lokalen Variablen der Funktion agent().
Ist die Funktion fertig oder stürzt das Programm ab, ist alles weg. Hier
machen wir den Zustand explizit: eine Sitzung enthält alles, was man braucht,
um später genau an derselben Stelle weiterzumachen.

  nachrichten     der vollständige Verlauf (System, User, Modell, Werkzeuge)
  schritte        alle Werkzeugaufrufe mit Ergebnis, für Ausgabe und Messung
  status          "laeuft", "wartet_auf_bestaetigung", "fertig" oder "abgebrochen"
  offene_aktion   der schreibende Werkzeugaufruf, der auf ein Ja oder Nein wartet

Ein Checkpoint ist die Sitzung als JSON-Datei unter sitzungen/<id>.json.
Frameworks wie LangGraph nennen das "Checkpointer". Mehr ist es nicht.
"""

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

ORDNER = Path("sitzungen")
MAX_NACHRICHTEN = 20  # Kurzzeit-Fenster: so viele Nachrichten sieht das Modell höchstens


@dataclass
class Sitzung:
    id: str
    nachrichten: list[dict] = field(default_factory=list)
    schritte: list[dict] = field(default_factory=list)
    status: str = "fertig"
    offene_aktion: dict | None = None
    antwort: str = ""
    modellaufrufe: int = 0
    eingabe_tokens: int = 0
    ausgabe_tokens: int = 0


def pfad(sitzungs_id: str) -> Path:
    # Path(...).name verhindert, dass eine ID wie ../../etc/x aus dem Ordner führt
    return ORDNER / f"{Path(sitzungs_id).name}.json"


def speichern(sitzung: Sitzung) -> Path:
    """Schreibt die Sitzung als JSON nach sitzungen/<id>.json (Checkpoint)."""
    # TODO 4a (Teil 1): Checkpoint schreiben
    #
    # Legt den ORDNER an, falls es ihn nicht gibt, und schreibt asdict(sitzung)
    # als JSON nach pfad(sitzung.id). ensure_ascii=False hält Umlaute lesbar.
    # Gebt den Pfad zurück.
    # Für Fortgeschrittene: Was passiert mit dem alten Checkpoint, wenn das
    # Programm mitten im Schreiben abstürzt? Schreibt erst in eine .tmp-Datei
    # und benennt sie dann um (Path.replace).
    ORDNER.mkdir(parents=True, exist_ok=True)
    path = pfad(sitzung.id)
    with path.open("w", encoding="utf-8") as f:
        json.dump(asdict(sitzung), f, ensure_ascii=False, indent=2)
    return path


def laden(sitzungs_id: str) -> Sitzung | None:
    """Liest eine gespeicherte Sitzung. Gibt None zurück, wenn es sie nicht gibt."""
    # TODO 4a (Teil 1): Checkpoint lesen
    #
    # Gibt es pfad(sitzungs_id) nicht, gebt None zurück. Sonst JSON lesen und
    # daraus wieder eine Sitzung machen: Sitzung(**daten).
    path = pfad(sitzungs_id)
    if not path.exists(): return None
    content = json.loads(path.read_text(encoding="utf-8"))
    return Sitzung(**content)


def kuerzen(nachrichten: list[dict], max_nachrichten: int = MAX_NACHRICHTEN) -> list[dict]:
    """Kurzzeit-Memory als Fenster: System-Prompt plus die letzten Nachrichten.

    Der Checkpoint behält den ganzen Verlauf, nur das Modell sieht weniger.
    Das Fenster beginnt immer bei einer Nachricht der Person. Sonst stünde
    am Anfang ein Werkzeugergebnis, dessen Aufruf abgeschnitten wurde, und
    die API lehnt den Verlauf ab oder das Modell versteht ihn nicht.
    """
    system = [m for m in nachrichten if m["role"] == "system"]
    rest = [m for m in nachrichten if m["role"] != "system"]
    if len(rest) <= max_nachrichten:
        return system + rest
    start = len(rest) - max_nachrichten
    nach = [i for i in range(start, len(rest)) if rest[i]["role"] == "user"]
    vor = [i for i in range(start) if rest[i]["role"] == "user"]
    # Lieber zu viel als die aktuelle Frage abschneiden
    start = nach[0] if nach else (vor[-1] if vor else 0)
    return system + rest[start:]


if __name__ == "__main__":
    # Alle gespeicherten Sitzungen anzeigen:
    #   docker compose run --rm app sitzung.py
    for datei in sorted(ORDNER.glob("*.json")):
        s = laden(datei.stem)
        print(f"{s.id:<20} {s.status:<26} {len(s.nachrichten):>3} Nachrichten  {s.eingabe_tokens:>6} Tokens")
