"""Den Agenten auf festen Aufgaben messen.

Aufruf:
    docker compose run --rm app messen.py                         # agent.v1
    docker compose run --rm app messen.py prompts/agent.v1.yaml prompts/agent.v2.yaml
    docker compose run --rm app messen.py workflow prompts/agent.v1.yaml
    docker compose run --rm app messen.py --zeigen prompts/agent.v1.yaml
    docker compose run --rm app messen.py prompts/agent.v1.yaml supervisor
    docker compose run --rm app messen.py --sitzungen               # Teil 4: Memory

Jede Variante ist entweder eine Prompt-Datei (dann läuft der Agent), das
Wort "workflow" (dann läuft der feste Routing-Workflow aus workflow.py) oder
das Wort "supervisor" (Supervisor mit Spezialisten aus supervisor.py).

Mit --sitzungen laufen stattdessen die Folgefragen aus aufgaben_sitzung.json
durch den Orchestrator, einmal mit und einmal ohne Langzeit-Memory.

Gemessen wird, was Huyen (AI Engineering, Kap. 6) für Agenten vorschlägt:
  - Antwort richtig: enthält die Antwort einen der erwarteten Begriffe?
  - Werkzeuge richtig: wurden die nötigen Werkzeuge benutzt?
  - ungültige Aufrufe: Werkzeug gibt es nicht oder Argumente passen nicht
  - Fehlerergebnisse: gültiger Aufruf, aber das Werkzeug meldet einen Fehler
    (meist ein falscher Wert, etwa datum="heute")
  - Effizienz: Werkzeugaufrufe, Modellaufrufe, Tokens und Dauer pro Aufgabe

Schreibende Werkzeuge werden beim Messen automatisch bestätigt.
"""

import argparse
import json
import os
import time

import httpx
from dataclasses import dataclass

from agent import Ergebnis, Schritt, agent, alle_werkzeuge, zeige_schritt
from llm import MODEL
from prompts import lade

AUFGABEN = "aufgaben.json"
AUFGABEN_SITZUNG = "aufgaben_sitzung.json"


@dataclass
class Messung:
    typ: str
    richtig: bool
    werkzeuge_ok: bool
    ergebnis: Ergebnis


def pruefen(antwort: str, erwartet: list[str]) -> bool:
    return any(e.lower() in antwort.lower() for e in erwartet)


def ausfuehren(variante: str, aufgabe: str, werkzeuge) -> Ergebnis:
    if variante == "workflow":
        from workflow import workflow
        return workflow(aufgabe, werkzeuge)
    if variante == "supervisor":
        from supervisor import supervisor
        return supervisor(aufgabe, werkzeuge, bestaetigen=lambda *_: True)
    return agent(aufgabe, werkzeuge, lade(variante), bestaetigen=lambda *_: True)


def buchungen_zuruecksetzen() -> None:
    """Jede Variante startet mit derselben Raumbelegung, ohne alte Buchungen."""
    url = os.environ.get("MCP_URL", "http://campus-mcp:8000/mcp").removesuffix("/mcp") + "/zuruecksetzen"
    try:
        httpx.post(url, timeout=5)
    except httpx.HTTPError:
        print(f"  Hinweis: {url} nicht erreichbar, Buchungen wurden nicht zurückgesetzt.")


def messen(variante: str, aufgaben: list[dict], werkzeuge, zeigen: bool) -> list[Messung]:
    print(f"\n=== {variante} · {MODEL}", flush=True)
    buchungen_zuruecksetzen()
    messungen = []
    for a in aufgaben:
        e = ausfuehren(variante, a["aufgabe"], werkzeuge)
        benutzt = {s.werkzeug for s in e.schritte if s.fehler != "unbekannt"}
        m = Messung(a["typ"], pruefen(e.antwort, a["erwartet"]), set(a["werkzeuge"]) <= benutzt, e)
        messungen.append(m)
        marke = "✓" if m.richtig else "✗"
        print(f"{marke} {a['aufgabe'][:58]:<58} {len(e.schritte)} Aufrufe  {e.sekunden:5.1f} s", flush=True)
        if zeigen or not m.richtig:
            for s in e.schritte:
                zeige_schritt(s)
            print(f"    Antwort: {e.antwort[:200].replace(chr(10), ' ')}")
    return messungen


def tabelle(ergebnisse: dict[str, list[Messung]]) -> None:
    n = len(next(iter(ergebnisse.values())))
    print(f"\nModell: {MODEL} · {n} Aufgaben\n")
    print(f"{'Variante':<24} {'Antwort':>8} {'Werkz.':>7} {'ungültig':>9} {'Fehler':>7} "
          f"{'Abbruch':>8} {'Aufrufe Ø':>10} {'Modell Ø':>9} {'Tokens Ø':>9} {'Dauer Ø':>8}")
    for name, ms in ergebnisse.items():
        schritte = [s for m in ms for s in m.ergebnis.schritte]
        print(
            f"{name:<24} {sum(m.richtig for m in ms):>4}/{n:<3} {sum(m.werkzeuge_ok for m in ms):>3}/{n:<3}"
            f" {sum(s.fehler in ('unbekannt', 'argumente') for s in schritte):>9}"
            f" {sum(s.fehler == 'wert' for s in schritte):>7}"
            f" {sum(m.ergebnis.abgebrochen for m in ms):>8}"
            f" {len(schritte) / n:>10.1f}"
            f" {sum(m.ergebnis.modellaufrufe for m in ms) / n:>9.1f}"
            f" {sum(m.ergebnis.eingabe_tokens for m in ms) / n:>9.0f}"
            f" {sum(m.ergebnis.sekunden for m in ms) / n:>7.1f}s"
        )
    print("\nRichtige Antworten nach Typ:")
    typen = list(dict.fromkeys(m.typ for m in next(iter(ergebnisse.values()))))
    print(f"{'Variante':<24} " + " ".join(f"{t:>11}" for t in typen))
    for name, ms in ergebnisse.items():
        zellen = []
        for t in typen:
            auswahl = [m for m in ms if m.typ == t]
            zellen.append(f"{sum(m.richtig for m in auswahl)}/{len(auswahl)}")
        print(f"{name:<24} " + " ".join(f"{z:>11}" for z in zellen))


# ---------------------------------------------------------------------------
# Teil 4: Folgefragen über mehrere Runden und Sitzungen (aufgaben_sitzung.json)
# ---------------------------------------------------------------------------

def sitzung_messen(aufgabe: dict, werkzeuge, mit_memory: bool, fenster: int, zeigen: bool) -> dict:
    """Spielt eine Aufgabe mit allen Sitzungen und Runden durch, Bestätigungen automatisch."""
    import memory
    from orchestrator import ausfuehren, entscheiden, frage_stellen
    from sitzung import Sitzung

    memory.speichern([])  # jede Aufgabe startet mit leerem Memory
    prompt = lade("prompts/agent.v1.yaml")
    wz = werkzeuge + (memory.MEMORY_WERKZEUGE if mit_memory else [])
    z = {"runden": 0, "richtig": 0, "aufrufe": 0, "tokens": 0, "sekunden": 0.0, "werkzeuge_ok": 0}
    for k, runden in enumerate(aufgabe["sitzungen"]):
        s = Sitzung(id=f"_messen_{aufgabe['name']}_{k}")  # neue Sitzung: Kurzzeit-Memory leer
        for r in runden:
            start = time.perf_counter()
            n_schritte, n_aufrufe, n_tokens = len(s.schritte), s.modellaufrufe, s.eingabe_tokens
            frage_stellen(s, r["frage"], prompt, mit_memory)
            ausfuehren(s, wz, fenster=fenster, temperature=prompt.temperature)
            while s.status == "wartet_auf_bestaetigung":  # beim Messen immer ja
                entscheiden(s, True, wz)
                ausfuehren(s, wz, fenster=fenster, temperature=prompt.temperature)
            benutzt = {x["werkzeug"] for x in s.schritte[n_schritte:]}
            # Gezählt wird die Antwort plus die Argumente der Werkzeugaufrufe dieser Runde.
            # So zählt auch eine Buchung "für Lerngruppe Mathe", die die Antwort nicht wiederholt.
            argumente = " ".join(str(x["argumente"]) for x in s.schritte[n_schritte:])
            ok = (not r["erwartet"] or pruefen(s.antwort + " " + argumente, r["erwartet"])) and \
                not pruefen(s.antwort, r.get("nicht_erwartet", []))
            if r["erwartet"]:  # Runden ohne Erwartung (nur merken) zählen nicht als Treffer
                z["runden"] += 1
                z["richtig"] += ok
            z["werkzeuge_ok"] += set(r["werkzeuge"]) <= benutzt
            z["aufrufe"] += s.modellaufrufe - n_aufrufe
            z["tokens"] += s.eingabe_tokens - n_tokens
            z["sekunden"] += time.perf_counter() - start
            marke = "·" if not r["erwartet"] else ("✓" if ok else "✗")
            print(f"  {marke} [{k + 1}] {r['frage'][:55]:<55} {s.modellaufrufe - n_aufrufe} Modellaufrufe", flush=True)
            if zeigen or (r["erwartet"] and not ok) or not set(r["werkzeuge"]) <= benutzt:
                for x in s.schritte[n_schritte:]:
                    zeige_schritt(Schritt(**x))
                print(f"      Antwort: {s.antwort[:200].replace(chr(10), ' ')}")
    return z


def sitzungen_messen(werkzeuge, fenster: int, zeigen: bool) -> None:
    import memory
    memory.DATEI = memory.Path("sitzungen/_messen_memory.json")  # euer memory.json bleibt unberührt
    memory.DATEI.parent.mkdir(exist_ok=True)
    with open(AUFGABEN_SITZUNG, encoding="utf-8") as f:
        aufgaben = json.load(f)
    zeilen = []
    for mit_memory in (True, False):
        name = "mit Memory" if mit_memory else "ohne Memory"
        print(f"\n=== Orchestrator {name} · Fenster {fenster} · {MODEL}", flush=True)
        buchungen_zuruecksetzen()
        for a in aufgaben:
            print(f"{a['name']} ({a['typ']})", flush=True)
            zeilen.append((name, a["typ"], sitzung_messen(a, werkzeuge, mit_memory, fenster, zeigen)))
    print(f"\nModell: {MODEL} · {len(aufgaben)} Aufgaben · Fenster {fenster} Nachrichten\n")
    print(f"{'Variante':<14} {'Typ':<10} {'Treffer':>8} {'Werkz.':>7} {'Aufrufe Σ':>10} {'Tokens Σ':>9} {'Dauer Σ':>8}")
    for name in ("mit Memory", "ohne Memory"):
        for typ in dict.fromkeys(t for _, t, _ in zeilen):
            zs = [z for n, t, z in zeilen if n == name and t == typ]
            summe = {k: sum(z[k] for z in zs) for k in zs[0]}
            runden_gesamt = sum(len(r) for a in aufgaben if a["typ"] == typ for r in a["sitzungen"])
            print(f"{name:<14} {typ:<10} {summe['richtig']:>4}/{summe['runden']:<3} "
                  f"{summe['werkzeuge_ok']:>3}/{runden_gesamt:<3} {summe['aufrufe']:>10} {summe['tokens']:>9} "
                  f"{summe['sekunden']:>7.0f}s")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("varianten", nargs="*", default=["prompts/agent.v1.yaml"])
    parser.add_argument("--ohne-mcp", action="store_true")
    parser.add_argument("--zeigen", action="store_true", help="alle Schritte zeigen, nicht nur bei Fehlern")
    parser.add_argument("--sitzungen", action="store_true", help="Folgefragen aus aufgaben_sitzung.json messen")
    parser.add_argument("--fenster", type=int, default=20, help="Kurzzeit-Fenster für --sitzungen")
    args = parser.parse_args()

    if args.sitzungen:
        sitzungen_messen(alle_werkzeuge(not args.ohne_mcp), args.fenster, args.zeigen)
        raise SystemExit

    with open(AUFGABEN, encoding="utf-8") as f:
        aufgaben = json.load(f)
    werkzeuge = alle_werkzeuge(not args.ohne_mcp)
    ergebnisse = {v.removeprefix("prompts/"): messen(v, aufgaben, werkzeuge, args.zeigen) for v in args.varianten}
    tabelle(ergebnisse)
