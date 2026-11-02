"""
Ablauf:
    1. Kennungen per regulärem Ausdruck aus der Frage holen.
    2. Gibt es welche, kommen die Dateien, in denen sie wörtlich stehen,
       in den Kontext (exakter Treffer, keine Teilwörter: "§ 1" findet
       nicht "§ 12").
    3. Gibt es keine Kennung oder keinen Treffer, sucht RAG wie bisher.
"""

import re
import sys
import time

import rag  # RAG-Suche (Embedding + Qdrant)
# Treffer: Datenklasse mit datei, text, wert
# antworten: fragt das Modell mit dem Kontext
# dokumente: alle Dateien als Dict {datei: text}
# zeigen: gibt die Antwort aus
from wissen import Treffer, antworten, dokumente, zeigen

# Höchstens so viele Dateien kommen bei der Textsuche in den Kontext.
MAX_DATEIEN = 3

# Raumnummern (F-201, A-010) und Paragraphen (§ 17, Paragraph 17).
# Drei Alternativen:
#   \b[A-Z]{1,3}-\d{2,4}\b  1-3 Buchstaben, Bindestrich, 2-4 Ziffern (F-201)
#   §\s*\d+                 Paragraphenzeichen, optional Leerzeichen, Ziffern
#   \bParagraph\s+\d+       ausgeschriebenes Wort mit Nummer
# IGNORECASE: "f-201" wird ebenfalls erkannt.
KENNUNG = re.compile(r"\b[A-Z]{1,3}-\d{2,4}\b|§\s*\d+|\bParagraph\s+\d+", re.IGNORECASE)

def kennungen(frage: str) -> list[str]:
    """Holt die Kennungen aus der Frage und bringt sie in eine einheitliche Form."""
    gefunden = []
    for k in KENNUNG.findall(frage):
        # Die Zahl herausziehen (bei "§ 17" ist das "17").
        nummer = re.search(r"\d+", k).group()
        if k.startswith("§") or k.lower().startswith("paragraph"):
            # "§17", "§ 17" und "Paragraph 17" werden alle zu "§ 17".
            gefunden.append(f"§ {nummer}")
        else:
            # Raumnummern nur großschreiben: "f-201" -> "F-201".
            gefunden.append(k.upper())
    return gefunden


def textsuche(begriffe: list[str]) -> list[Treffer]:
    """Exakte Suche: Welche Dateien enthalten die Begriffe wörtlich?"""
    treffer = []
    for datei, text in dokumente().items():
        wert = 0  # Anzahl der Fundstellen in dieser Datei
        for b in begriffe:
            # "§ 17" soll auch "§17" finden, aber nicht "§ 170".
            # re.escape entschärft Sonderzeichen; das (escapte) Leerzeichen
            # wird zu "beliebig viele Leerzeichen".
            muster = re.escape(b).replace(r"\ ", r"\s*")
            # (?<![\w-]): davor kein Buchstabe, keine Ziffer, kein Bindestrich
            #             (also nicht "XF-201").
            # (?!\d):     danach keine Ziffer (also nicht "F-2010" oder "§ 170").
            wert += len(re.findall(rf"(?<![\w-]){muster}(?!\d)", text, re.IGNORECASE))
        if wert:
            treffer.append(Treffer(datei, text, wert))
    # Dateien mit den meisten Fundstellen zuerst, auf MAX_DATEIEN kürzen.
    treffer.sort(key=lambda t: t.wert, reverse=True)
    return treffer[:MAX_DATEIEN]


def abrufen(frage: str) -> tuple[list[Treffer], float]:
    """Hybrid-Suche: erst exakte Textsuche, sonst RAG. Gibt auch die Dauer zurück."""
    start = time.perf_counter()
    begriffe = kennungen(frage)
    # Textsuche nur, wenn die Frage überhaupt eine Kennung enthält.
    treffer = textsuche(begriffe) if begriffe else []
    # Keine Kennung oder kein wörtlicher Treffer: RAG übernimmt.
    if not treffer:
        treffer = rag.suche(frage)
    return treffer, time.perf_counter() - start


# Aufruf auf der Kommandozeile: python hybrid.py Wo ist Raum F-201?
if __name__ == "__main__":
    frage = " ".join(sys.argv[1:])
    print(f"Kennungen: {kennungen(frage) or '(keine, also RAG)'}")
    treffer, sekunden = abrufen(frage)
    # wert ist bei der Textsuche eine Trefferanzahl, bei RAG eine Ähnlichkeit.
    for t in treffer:
        print(f"  {t.wert:>6.3f}  {t.datei}")
    print()
    zeigen(antworten(frage, treffer, sekunden))
