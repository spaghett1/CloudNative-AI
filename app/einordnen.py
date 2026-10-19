"""Teil D: Anfragen an den Campus-Assistenten strukturiert einordnen.

Das Modell soll aus einer Mail drei Dinge herauslesen: das Anliegen,
den Studiengang und eine Frist. Das Ergebnis verarbeitet ein Programm
weiter, es muss also immer die gleiche Form haben.

Aufruf:
    docker compose run --rm app einordnen.py
    docker compose run --rm app einordnen.py "Eure eigene Mail"

Eure Aufgaben stehen bei den TODO-Kommentaren.
"""

import json
import sys
from typing import Literal

from pydantic import BaseModel, ValidationError

from llm import MODEL, client

SYSTEM_PROMPT = (
    "Du ordnest Mails von Studierenden an die HTWG Konstanz ein. "
    "Antworte ausschließlich mit JSON. "
    "anliegen ist pruefung, mensa, bibliothek oder sonstiges. "
    "studiengang ist das Kürzel (z. B. AIN, WIN, MSI) oder null. "
    "frist ist die Frist aus der Mail, so wie sie dort steht, oder null."
)


class Anfrage(BaseModel):
    anliegen: Literal["pruefung", "mensa", "bibliothek", "sonstiges"]
    studiengang: str | None
    frist: str | None


def schema_von(modell: type[BaseModel]) -> dict:
    """Verpackt das JSON-Schema einer Pydantic-Klasse für response_format."""
    return {
        "type": "json_schema",
        "json_schema": {
            "name": modell.__name__.lower(),
            "schema": modell.model_json_schema(),
        },
    }


def einordnen(mail: str, versuche: int = 3) -> Anfrage:
    """Fragt das Modell und gibt eine geprüfte Anfrage zurück."""
    nachricht = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": mail},
    ]

    #TODOS---------------------------------------------------------------------------------
    for versuch in range(1, versuche + 1):
        try:
            # TODO 1: Modell aufrufen mit niedriger temperature für deterministische Extraktion
            antwort = client.chat.completions.create(
                model=MODEL,
                messages=nachricht,
                temperature=0.0,
                #response_format=schema_von(Anfrage),
            )
            inhalt = antwort.choices[0].message.content or ""

            return inhalt
            # TODO 2: Validierung mittels Pydantic
            #return Anfrage.model_validate_json(inhalt)

        except ValidationError as e:
            # TODO 3: Fehlversuch abfangen und Meldung ausgeben
            print(f"[Warnung] Validierungsfehler in Versuch {versuch}/{versuche}: {e}")
            if versuch == versuche:
                raise RuntimeError(
                    f"Konnte Mail nach {versuche} Versuchen nicht valide parsen."
                ) from e
    #---------------------------------------------------------------------------------  
    raise NotImplementedError("TODO: einordnen() umsetzen")

def fragen():
    with open("mails.json", encoding="utf-8") as f:
            faelle = json.load(f)
    for fall in faelle:
        ergebnis = einordnen(fall["mail"])
        print(ergebnis)


def auswerten() -> None:
    """Ordnet alle Test-Mails ein und vergleicht mit dem Erwarteten."""
    with open("mails.json", encoding="utf-8") as f:
        faelle = json.load(f)
    richtig = 0
    for fall in faelle:
        ergebnis = einordnen(fall["mail"])
        # TODO 4: Vergleicht ergebnis mit fall["erwartet"]. Zählt einen
        #         Fall als richtig, wenn das Anliegen stimmt. Gebt für
        #         jeden Fall Mail, Ergebnis und ✓ oder ✗ aus.
        erwartetes_anliegen = fall["erwartet"]["anliegen"]

        ist_korrekt = ergebnis.anliegen == erwartetes_anliegen
        if ist_korrekt:
            richtig += 1

        symbol = "✓" if ist_korrekt else "✗"

        print(f"Mail:      {fall['mail']}")
        print(f"Ergebnis:  {ergebnis.model_dump()}")
        print(f"Erwartet:  {fall['erwartet']}")
        print(f"Status:    {symbol}\n" + "-" * 50)

    print(f"\n{richtig} von {len(faelle)} Anliegen richtig ({MODEL})")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        print(einordnen(" ".join(sys.argv[1:])).model_dump_json(indent=2))
    else:
        #auswerten()
        fragen()
