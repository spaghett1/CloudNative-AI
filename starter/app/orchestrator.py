"""Die Agent-Schleife als expliziter Zustandsautomat, mit Checkpoint und Unterbrechung.

Aufruf:
    docker compose run --rm app orchestrator.py --sitzung kim "Ist O-108 am Freitag um 14 Uhr frei?"
    docker compose run --rm app orchestrator.py --sitzung kim "Dann buch ihn mir für die Lerngruppe Kim."
    docker compose run --rm app orchestrator.py --sitzung kim --bestaetigen
    docker compose run --rm app orchestrator.py --sitzung kim --ablehnen
    docker compose run --rm app orchestrator.py --sitzung kim --verlauf

Gleiche Schleife wie in agent.py, aber in Knoten zerlegt. Jeder Aufruf von
schritt() führt genau einen Übergang aus und ändert nur die Sitzung:

                ┌──────────────── Ergebnis angehängt ───────────────┐
                ▼                                                   │
    frage ─▶ modell_fragen ── Werkzeugaufrufe ─▶ aufruf_bearbeiten ─┤
                │                                   │ schreibend    │
                │ nur Text                          ▼               │
                ▼                         wartet_auf_bestaetigung   │
             antworten ─▶ fertig            (Prozess endet)         │
                                                    │ --bestaetigen │
                                                    └─ entscheiden ─┘

Nach jedem Übergang schreibt ausfuehren() einen Checkpoint. Wartet die
Sitzung auf eine Bestätigung, endet das Programm einfach. Später macht ein
neuer Prozess mit --sitzung ID --bestaetigen genau dort weiter.
"""

import argparse
import json
import time
from datetime import datetime
from types import SimpleNamespace

from agent import STANDARD_PROMPT, MAX_SCHRITTE, Schritt, alle_werkzeuge, fragen, werkzeug_ausfuehren, zeige_schritt
from llm import MODEL, client
from memory import MEMORY_WERKZEUGE, memory_prompt
from prompts import Prompt, lade
from sitzung import MAX_NACHRICHTEN, Sitzung, kuerzen, laden, speichern
from werkzeuge import Werkzeug

ABGELEHNT = "Abgelehnt: Die Person hat diese Aktion nicht bestätigt. Führe sie nicht aus."


# ---------------------------------------------------------------------------
# Hilfen
# ---------------------------------------------------------------------------

def als_aufruf(aufruf: dict) -> SimpleNamespace:
    """Macht aus einem gespeicherten Aufruf (dict) wieder ein Objekt mit
    .id und .function.name/.arguments, wie werkzeug_ausfuehren() es erwartet."""
    f = aufruf["function"]
    return SimpleNamespace(id=aufruf["id"], function=SimpleNamespace(name=f["name"], arguments=f.get("arguments")))


def offene_aufrufe(sitzung: Sitzung) -> list[dict]:
    """Werkzeugaufrufe der letzten Modellnachricht, zu denen noch kein Ergebnis im Verlauf steht."""
    for i in range(len(sitzung.nachrichten) - 1, -1, -1):
        m = sitzung.nachrichten[i]
        if m["role"] == "user":
            return []
        if m["role"] == "assistant":
            erledigt = {n.get("tool_call_id") for n in sitzung.nachrichten[i + 1:] if n["role"] == "tool"}
            return [a for a in m.get("tool_calls") or [] if a["id"] not in erledigt]
    return []


def ergebnis_anhaengen(sitzung: Sitzung, aufruf_id: str, schritt: Schritt) -> None:
    sitzung.schritte.append(vars(schritt).copy())
    sitzung.nachrichten.append({"role": "tool", "tool_call_id": aufruf_id, "content": schritt.ergebnis})


# ---------------------------------------------------------------------------
# Knoten des Zustandsautomaten
# ---------------------------------------------------------------------------

def frage_stellen(sitzung: Sitzung, frage: str, prompt: Prompt, mit_memory: bool = True) -> Sitzung:
    """Neue Frage in eine (neue oder alte) Sitzung. Der System-Prompt wird jedes Mal
    neu gesetzt, damit er die aktuellen Fakten aus dem Langzeit-Memory enthält."""
    system, user = prompt.nachrichten(frage)
    if mit_memory:
        system["content"] += memory_prompt()
    if sitzung.nachrichten and sitzung.nachrichten[0]["role"] == "system":
        sitzung.nachrichten[0] = system
    else:
        sitzung.nachrichten.insert(0, system)
    sitzung.nachrichten.append(user)
    sitzung.status = "laeuft"
    sitzung.antwort = ""
    return sitzung


def modell_fragen(sitzung: Sitzung, werkzeuge: list[Werkzeug], fenster: int, temperature: float) -> Sitzung:
    antwort = client.chat.completions.create(
        model=MODEL,
        messages=kuerzen(sitzung.nachrichten, fenster),  # Kurzzeit-Fenster
        tools=[w.als_tool() for w in werkzeuge],
        temperature=temperature,
        max_tokens=500,
    )
    sitzung.modellaufrufe += 1
    sitzung.eingabe_tokens += antwort.usage.prompt_tokens
    sitzung.ausgabe_tokens += antwort.usage.completion_tokens
    nachricht = antwort.choices[0].message
    gespeichert = nachricht.model_dump(exclude_none=True)
    for k, aufruf in enumerate(gespeichert.get("tool_calls") or []):
        if not aufruf.get("id"):  # manche Server liefern keine ID, wir brauchen eine
            aufruf["id"] = f"aufruf_{len(sitzung.nachrichten)}_{k}"
    sitzung.nachrichten.append(gespeichert)
    if not nachricht.tool_calls:
        return antworten(sitzung, nachricht.content or "")
    return sitzung


def aufruf_bearbeiten(sitzung: Sitzung, aufruf: dict, verzeichnis: dict[str, Werkzeug]) -> Sitzung:
    """Führt einen Werkzeugaufruf aus, oder unterbricht vor einem schreibenden."""
    werkzeug = verzeichnis.get(aufruf["function"]["name"])

    # TODO 4b: Unterbrechung vor schreibenden Werkzeugen
    #
    # Im Moment fragt werkzeug_ausfuehren() wie in Teil 2 im Terminal nach und
    # blockiert, bis jemand tippt. Ändert das: Ist das Werkzeug schreibend,
    # führt es NICHT aus, sondern setzt sitzung.status auf
    # "wartet_auf_bestaetigung", merkt euch den Aufruf in sitzung.offene_aktion
    # und gebt die Sitzung zurück. ausfuehren() hört dann auf, der Checkpoint
    # ist geschrieben, das Programm endet. Weiter geht es mit entscheiden().

    if werkzeug is not None and werkzeug.schreibend:
        sitzung.status = "wartet_auf_bestaetigung"
        sitzung.offene_aktion = aufruf  # der Aufruf als dict, damit er im Checkpoint steht
        return sitzung

    schritt = werkzeug_ausfuehren(als_aufruf(aufruf), verzeichnis, bestaetigen=fragen)
    ergebnis_anhaengen(sitzung, aufruf["id"], schritt)
    return sitzung


def entscheiden(sitzung: Sitzung, ja: bool, werkzeuge: list[Werkzeug]) -> Sitzung:
    """Die Person hat entschieden: offene Aktion ausführen oder ablehnen, dann weiter."""
    aufruf = sitzung.offene_aktion
    if sitzung.status != "wartet_auf_bestaetigung" or not aufruf:
        raise SystemExit(f"Sitzung {sitzung.id} wartet auf keine Bestätigung (Status: {sitzung.status}).")
    if ja:
        verzeichnis = {w.name: w for w in werkzeuge}
        schritt = werkzeug_ausfuehren(als_aufruf(aufruf), verzeichnis, bestaetigen=lambda *_: True)
    else:
        schritt = Schritt(aufruf["function"]["name"], aufruf["function"].get("arguments") or "", ABGELEHNT,
                          "abgelehnt")
    ergebnis_anhaengen(sitzung, aufruf["id"], schritt)
    sitzung.offene_aktion = None
    sitzung.status = "laeuft"
    return sitzung


def antworten(sitzung: Sitzung, text: str) -> Sitzung:
    sitzung.antwort = text.strip()
    sitzung.status = "fertig"
    return sitzung


def schritt(sitzung: Sitzung, werkzeuge: list[Werkzeug], fenster: int = MAX_NACHRICHTEN,
            temperature: float = 0.0) -> Sitzung:
    """Ein Übergang. Welcher Knoten dran ist, entscheidet allein der Zustand."""
    if sitzung.status != "laeuft":
        return sitzung
    offen = offene_aufrufe(sitzung)
    if offen:
        return aufruf_bearbeiten(sitzung, offen[0], {w.name: w for w in werkzeuge})
    return modell_fragen(sitzung, werkzeuge, fenster, temperature)


def ausfuehren(sitzung: Sitzung, werkzeuge: list[Werkzeug], max_schritte: int = MAX_SCHRITTE,
               fenster: int = MAX_NACHRICHTEN, temperature: float = 0.0, zeigen: bool = False) -> Sitzung:
    """Führt Übergänge aus, bis die Sitzung nicht mehr läuft, und speichert nach jedem."""
    aufrufe_vorher = sitzung.modellaufrufe
    while sitzung.status == "laeuft":
        if sitzung.modellaufrufe - aufrufe_vorher >= max_schritte and not offene_aufrufe(sitzung):
            sitzung.status = "abgebrochen"
            sitzung.antwort = f"Abgebrochen nach {max_schritte} Modellaufrufen ohne Antwort."
            break
        n = len(sitzung.schritte)
        schritt(sitzung, werkzeuge, fenster, temperature)
        if zeigen:
            for s in sitzung.schritte[n:]:
                zeige_schritt(Schritt(**s))

        # TODO 4a (Teil 2): nach jedem Übergang einen Checkpoint schreiben
        #
        # Eine Zeile. Wo genau sie steht, entscheidet, wie viel Arbeit bei
        # einem Absturz verloren geht.
        speichern(sitzung)

    speichern(sitzung)
    return sitzung


# ---------------------------------------------------------------------------
# Kommandozeile
# ---------------------------------------------------------------------------

def zeige_status(s: Sitzung) -> None:
    if s.status == "wartet_auf_bestaetigung":
        f = s.offene_aktion["function"]
        print(f"\n  ⏸ Wartet auf Bestätigung: {f['name']}({f.get('arguments')})")
        print(f"    Weiter mit:  orchestrator.py --sitzung {s.id} --bestaetigen   (oder --ablehnen)")
    else:
        print(f"\n{s.antwort}")
    print(f"\n  Sitzung {s.id} · {s.status} · {len(s.nachrichten)} Nachrichten · {s.modellaufrufe} Modellaufrufe · "
          f"{s.eingabe_tokens} Eingabe-Tokens gesamt · {MODEL}")


def zeige_verlauf(s: Sitzung) -> None:
    for m in s.nachrichten:
        if m["role"] == "system":
            continue
        if m.get("tool_calls"):
            text = ", ".join(f"{a['function']['name']}({a['function'].get('arguments')})" for a in m["tool_calls"])
            print(f"  {m['role']:<9} → {text}")
        else:
            print(f"  {m['role']:<9} {str(m.get('content', '')).replace(chr(10), ' ')[:150]}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Campus-Agent mit Sitzung, Checkpoint und Memory")
    parser.add_argument("frage", nargs="?")
    parser.add_argument("--sitzung", default=datetime.now().strftime("s%m%d-%H%M%S"), help="ID der Sitzung")
    gruppe = parser.add_mutually_exclusive_group()
    gruppe.add_argument("--bestaetigen", action="store_true", help="offene Aktion ausführen")
    gruppe.add_argument("--ablehnen", action="store_true", help="offene Aktion ablehnen")
    gruppe.add_argument("--verlauf", action="store_true", help="gespeicherten Verlauf zeigen")
    parser.add_argument("--prompt", default=STANDARD_PROMPT)
    parser.add_argument("--ohne-memory", action="store_true", help="kein Langzeit-Memory")
    parser.add_argument("--ohne-mcp", action="store_true")
    parser.add_argument("--max-schritte", type=int, default=MAX_SCHRITTE)
    parser.add_argument("--fenster", type=int, default=MAX_NACHRICHTEN, help="Kurzzeit-Fenster in Nachrichten")
    args = parser.parse_args()

    # TODO 4a (Teil 3): eine vorhandene Sitzung laden, sonst eine neue anlegen
    #
    # Im Moment beginnt jeder Aufruf bei null. Nutzt laden(args.sitzung) und
    # legt nur dann eine neue Sitzung an, wenn es noch keine gibt.
    restored = laden(args.sitzung)
    if restored is None:
        sitzung = Sitzung(id=args.sitzung)
    else:
        sitzung = restored

    if args.verlauf:
        zeige_verlauf(sitzung)
        raise SystemExit
    werkzeuge = alle_werkzeuge(not args.ohne_mcp) + ([] if args.ohne_memory else MEMORY_WERKZEUGE)
    prompt = lade(args.prompt)

    if args.bestaetigen or args.ablehnen:
        entscheiden(sitzung, args.bestaetigen, werkzeuge)
        zeige_schritt(Schritt(**sitzung.schritte[-1]))
    elif args.frage:
        if sitzung.status == "wartet_auf_bestaetigung":
            raise SystemExit("Die Sitzung wartet noch auf --bestaetigen oder --ablehnen.")
        frage_stellen(sitzung, args.frage, prompt, not args.ohne_memory)
    else:
        parser.error("Frage, --bestaetigen, --ablehnen oder --verlauf angeben")

    ausfuehren(sitzung, werkzeuge, args.max_schritte, args.fenster, prompt.temperature, zeigen=True)
    zeige_status(sitzung)
