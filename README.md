# CloudNative-AI
Abgaben im Fach CloudeNative-AI.


Abgabe 1:

Teil 1 - Setup und erster Modellaufruf 

1. Im Setup laufen drei definierte Container: ollama, modell-laden und app.
Ollama ist der zentrale LLM-Container, modell-laden ist zum vorab-herunterladen der Modelle, app ist der eigentliche python container, der nach app-laden startet.
Die Anwendung findet das Modell ueber das interne Docker-Netzwerk unter ollama:11434. Welches Modell genutzt wird, steht in der .env-Datei.

2. 
|Frage |Modell |TTF(ms) |Gesamtdauer(ms) | Antwort brauchbar? |
| - | - | - | - | - |
| Wo finde ich meinen Stundenplan? | llama3.2 | 4456 | 11324 | ja |
| - | qwen2.5 | 231 | 483| nein |
| An wen kann ich mich wenden, wenn ich ein Problem habe? | llama3.2 | 242 | 6826 | ja |
| - | qwen2.5 | 119 | 485 | nein |
| Wieviele Studierende hat die Hochschule | llama3.2 | 208 | 1149 | ja |
| - | qwen 2.5 | 108 | 736 | nein |

Teil 2 - Modell austauschen

2. 
Antwort llama3.2: 
Die Hochschule für Technologie, Wirtschaft und Geisteswissenschaften (HTWG) Konstanz ist eine staatliche Hochschule in Deutschland.
	
Antwort qwen2.5: 
Das HTWG Konzert ist nicht privater oder staatliche Hochschule. Es stellt sich an auf die Entwicklung eines hochintensiv anstehenden Forschungszentrums in der Bildungskommunikation und Kulturalleitgestaltung zusammen.

Teil 3 - Sampling und Tokens messen
1.
Temperature 0: 5x dieselbe Antwort
Temperature 0.7: 5 verschiedene Antworten 
Temperature 1.2: 5 verschiedene Antworten

2. Eine niedrige Temperature vermeidet Inkonsistenz der Antworten

3. 
Temperature 0.0: 1 verschiedene Antworten bei 5 Durchläufen
   5× Die HTWG Konstanz ist eine staatliche Hochschule.

Temperature 0.7: 3 verschiedene Antworten bei 5 Durchläufen
   2× Die Hochschule für Technik und Wirtschaft (HTWG) Konstanz ist eine staatliche Hochschule.
   2× Die HTWG Konstanz ist eine staatliche Hochschule.
   1× Die HTWG (Hochschule für Technik und Wirtschaft) Konstanz ist eine staatliche Hochschule.

Temperature 1.2: 5 verschiedene Antworten bei 5 Durchläufen
   1× Die Hochschule für Technologie, Wirtschaft und Geopolitek (HTWG) Konstanz in Deutschland ist eine staatliche Hochschule.
   1× Die Hochschule für Technik und Wirtschaft (HTWG) Klettgauturm in Konstanz ist eine staatliche Hochschule, da sie von der baden-würt
   1× Die Hochschule für Technologie, Wirtschaft und Geo- und Umweltsciences Konstanz (HTWG) Konstanz ist eine staatliche Hochschule.
   1× Die Hochschule fürTechnik und Wirtschaft (HTWG) in Konstanz ist eine staatliche Hochschule.
   1× Die Hochschule Thomas-Müntzer-Werkstatt (HTWG) Konstanz ist eine staatliche Fachhochschule in Deutschland.

Tokens
1. Der deutsche Text braucht 14 Tokens mehr
2. 
Deutscher Text: Entspricht das   Informatikprüfungsordnungsdokument der HTWG Konstanz den Vorgaben der Hochschulprüfungsverwaltung?
Englischer Text: Does the document outlining the computer science examination regulations at HTWG Konstanz comply with the guidelines of the Higher Education Examination Administration?
=> kleinerer Unterschied: 10 tokens mehr (+ 20%)

Teil 4 - Anfragen strukturiert einordnen

1.
llama3.2: 7 von 8 richtig eingeordnet.
qwen2.5: 2 von 8 richtig eingeordnet.

2. Wir hatten Fehler im Anliegen als auch falscher Studiengang sowie Frist

3. Halluzination werden durchgelassen, da das Schema nur das Format prueft und nicht de Fakten.
"null" statt None wird durchgelassen da Json nur "null" als einzigen nicht-wert hat

4. Keines der Modelle laesst sich umleiten durch die Prompt injection, sie versuchen dennoch, den prompt wie eine email zu behandeln und das anliegen daraus zu extrahieren.


5.
Die ausgaben sind sehr inkonsistent. Das Modell beantwortet eher die als Frage gestellten Mails, als die Mails einzuordnen, aber versucht weitesgehend, den system prompt einzuhalten. die antwort ist immer json und die kategorie frist ist immer in diesem json enthalten. die anderen kategorien sind aber falsch oder gar nicht enthalten.
Bei der letzten Email mit der prompt injection lauft das programm nicht weiter, bzw steckt in einem loop fest.
Temperature beim Retry erhoehen
