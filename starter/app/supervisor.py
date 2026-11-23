"""Von einem Agenten zu mehreren: ein Supervisor verteilt an Spezialisten.

Aufruf:
    docker compose run --rm app supervisor.py "Ist O-108 am Donnerstag um 14 Uhr frei?"
    docker compose run --rm app supervisor.py "Was gibt es heute in der Mensa, und ist F-012 um 14 Uhr frei?"
    docker compose run --rm app messen.py prompts/agent.v1.yaml supervisor

Ablauf (Muster "Supervisor" bzw. "Orchestrator-Workers"):

  1. Supervisor (ein Modellaufruf, strukturierte Ausgabe): zerlegt die Frage in
     Aufträge an Spezialisten oder antwortet selbst, wenn keiner passt.
  2. Spezialisten: je ein eigener Agent, die Funktion agent() aus agent.py,
     mit kurzem Prompt und nur seinen Werkzeugen.
       raum_agent    raeume_auflisten, raum_frei, raum_buchen, heute
       mensa_agent   mensa_speiseplan, heute
       wissen_agent  suche_wissen, lies_datei, heute, tage_bis
  3. Ein Auftrag: die Antwort des Spezialisten ist die Antwort.
     Mehrere Aufträge: ein letzter Modellaufruf fasst zusammen (antwort.v1).

Gezählt wird alles: Modellaufrufe und Tokens von Supervisor und Spezialisten.
"""

import argparse
import os
import time
from typing import Literal

from pydantic import BaseModel

from agent import Ergebnis, agent, alle_werkzeuge, fragen, zeige_schritt
from llm import MODEL, client
from prompts import lade
from werkzeuge import Werkzeug
from workflow import schema_von

SUPERVISOR_PROMPT = os.environ.get("SUPERVISOR_PROMPT", "prompts/supervisor.v1.yaml")

# Spezialist: Prompt und erlaubte Werkzeuge. Fehlt ein Werkzeug (etwa tage_bis
# ohne TODO 2), wird es einfach weggelassen.
SPEZIALISTEN = {
    "raum": ("prompts/raum.v1.yaml", {"raeume_auflisten", "raum_frei", "raum_buchen", "heute"}),
    "mensa": ("prompts/mensa.v1.yaml", {"mensa_speiseplan", "heute"}),
    "wissen": ("prompts/wissen.v1.yaml", {"suche_wissen", "lies_datei", "heute", "tage_bis"}),
}


class Auftrag(BaseModel):
    spezialist: Literal["raum", "mensa", "wissen"]
    auftrag: str  # vollständige Frage an den Spezialisten, er sieht nur diesen Text


class Plan(BaseModel):
    # Beide Felder ohne Standardwert: Dann sind sie im JSON-Schema "required",
    # und das Modell kann die Aufträge nicht einfach weglassen.
    auftraege: list[Auftrag]
    antwort: str | None  # nur, wenn kein Spezialist passt, sonst null


def zaehlen(gesamt: Ergebnis, antwort) -> None:
    gesamt.modellaufrufe += 1
    gesamt.eingabe_tokens += antwort.usage.prompt_tokens
    gesamt.ausgabe_tokens += antwort.usage.completion_tokens


def planen(aufgabe: str, gesamt: Ergebnis) -> Plan:
    """Schritt 1: der Supervisor entscheidet, wer was macht."""
    prompt = lade(SUPERVISOR_PROMPT)
    antwort = client.chat.completions.create(
        model=MODEL, temperature=prompt.temperature, messages=prompt.nachrichten(aufgabe),
        max_tokens=300, response_format=schema_von(Plan))
    zaehlen(gesamt, antwort)
    try:
        return Plan.model_validate_json(antwort.choices[0].message.content or "")
    except ValueError:
        return Plan(auftraege=[], antwort="Dazu habe ich keine Information.")  # Plan kaputt: ehrlich bleiben


def spezialist(name: str, auftrag: str, werkzeuge: list[Werkzeug], bestaetigen=fragen,
               zeigen: bool = False) -> Ergebnis:
    """Ein Spezialist ist der bekannte Agent, nur mit eigenem Prompt und weniger Werkzeugen."""
    pfad, erlaubt = SPEZIALISTEN[name]
    eigene = [w for w in werkzeuge if w.name in erlaubt]
    return agent(auftrag, eigene, lade(pfad), bestaetigen=bestaetigen, zeigen=zeigen)


def raum_agent(auftrag: str, werkzeuge: list[Werkzeug], **kw) -> Ergebnis:
    return spezialist("raum", auftrag, werkzeuge, **kw)


def mensa_agent(auftrag: str, werkzeuge: list[Werkzeug], **kw) -> Ergebnis:
    return spezialist("mensa", auftrag, werkzeuge, **kw)


def wissen_agent(auftrag: str, werkzeuge: list[Werkzeug], **kw) -> Ergebnis:
    return spezialist("wissen", auftrag, werkzeuge, **kw)


AGENTEN = {"raum": raum_agent, "mensa": mensa_agent, "wissen": wissen_agent}


def zusammenfassen(aufgabe: str, teilantworten: list[tuple[str, str]], gesamt: Ergebnis) -> str:
    """Schritt 3 bei mehreren Aufträgen: aus den Teilantworten eine Antwort machen."""
    prompt = lade("prompts/antwort.v1.yaml")
    kontext = "\n".join(f"{name}: {text}" for name, text in teilantworten)
    eingabe = f"<werkzeug>\n{kontext}\n</werkzeug>\n\nFrage: {aufgabe}"
    antwort = client.chat.completions.create(
        model=MODEL, temperature=prompt.temperature, messages=prompt.nachrichten(eingabe), max_tokens=300)
    zaehlen(gesamt, antwort)
    return (antwort.choices[0].message.content or "").strip()


def supervisor(aufgabe: str, werkzeuge: list[Werkzeug], bestaetigen=fragen, zeigen: bool = False) -> Ergebnis:
    gesamt = Ergebnis(antwort="")
    start = time.perf_counter()
    plan = planen(aufgabe, gesamt)
    if zeigen:
        for a in plan.auftraege:
            print(f"  ⇒ {a.spezialist}_agent: {a.auftrag}")

    # TODO 4d: Aufträge an die Spezialisten verteilen
    #
    # Für jeden Auftrag a in plan.auftraege:
    #  1. den passenden Spezialisten aus AGENTEN aufrufen, mit a.auftrag, der
    #     vollen Werkzeugliste (er filtert selbst), bestaetigen und zeigen.
    #  2. (a.spezialist, e.antwort) an teilantworten anhängen.
    #  3. Schritte, Modellaufrufe, Eingabe- und Ausgabe-Tokens des Spezialisten
    #     in `gesamt` aufaddieren und abgebrochen übernehmen. Sonst misst
    #     messen.py nur den Supervisor, und der Vergleich ist geschönt.
    teilantworten = []
    for a in plan.auftraege:
        e = AGENTEN[a.spezialist](a.auftrag, werkzeuge, bestaetigen=bestaetigen, zeigen=zeigen)
        teilantworten.append((a.spezialist, e.antwort))
        gesamt.schritte += e.schritte
        gesamt.modellaufrufe += e.modellaufrufe
        gesamt.eingabe_tokens += e.eingabe_tokens
        gesamt.ausgabe_tokens += e.ausgabe_tokens
        gesamt.abgebrochen = gesamt.abgebrochen or e.abgebrochen

    if not teilantworten:
        gesamt.antwort = plan.antwort or "Dazu habe ich keine Information."
    elif len(teilantworten) == 1:
        gesamt.antwort = teilantworten[0][1]
    else:
        gesamt.antwort = zusammenfassen(aufgabe, teilantworten, gesamt)
    gesamt.sekunden = time.perf_counter() - start
    return gesamt


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Supervisor mit drei Spezialisten")
    parser.add_argument("aufgabe", nargs="?", default="Was gibt es heute in der Mensa?")
    args = parser.parse_args()
    e = supervisor(args.aufgabe, alle_werkzeuge(), zeigen=True)
    print(f"\n{e.antwort}\n")
    print(f"  {len(e.schritte)} Werkzeugaufrufe · {e.modellaufrufe} Modellaufrufe · "
          f"{e.eingabe_tokens} Eingabe-Tokens · {e.sekunden:.1f} s · {MODEL}")
