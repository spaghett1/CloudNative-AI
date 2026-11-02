"""Teil C: Wie viele Tokens kostet derselbe Inhalt auf Deutsch und Englisch?

Die API meldet in usage.prompt_tokens, wie viele Tokens die Eingabe hatte.
Darin stecken auch einige Steuer-Tokens des Chat-Formats, bei beiden
Sprachen gleich viele. Für den Vergleich zählt deshalb die Differenz.

Aufruf:
    docker compose run --rm app tokens.py
"""

from llm import MODEL, client

TEXTE = {
    "Deutsch": "Glaubst du wirklich, dass viele Menschen heute noch gerne neue Bücher lesen oder bevorzugen sie stattdessen lieber spannende Filme und interessante Serien auf dem Bildschirm?",
    "Englisch": "Do you really believe that many people today still like to read new books, or do they rather prefer exciting movies and interesting series instead?",
}


def eingabe_tokens(text: str) -> int:
    antwort = client.chat.completions.create(
        model=MODEL,
        messages=[{"role": "user", "content": text}],
        max_tokens=1,
    )
    return antwort.usage.prompt_tokens


print(f"Modell: {MODEL}\n")
ergebnis = {}
for sprache, text in TEXTE.items():
    ergebnis[sprache] = eingabe_tokens(text)
    print(f"{sprache:9} {ergebnis[sprache]:3} Tokens "
          f"für {len(text.split())} Wörter")
mehr = ergebnis["Deutsch"] - ergebnis["Englisch"]
print(f"\nDeutsch braucht {mehr} Tokens mehr "
      f"({ergebnis['Deutsch'] / ergebnis['Englisch'] - 1:+.0%}).")
