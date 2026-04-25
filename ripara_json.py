import json
import re

def ripara_database_locale():
    try:
        with open("giocatori.json", "r", encoding="utf-8") as f:
            giocatori = json.load(f)
        
        count_puliti = 0
        count_cancellati = 0
        
        for g in giocatori:
            if 'squadra' in g:
                # 1. Togliamo i due punti e gli spazi ai lati
                vecchio = g['squadra']
                nuovo = vecchio.strip(" : -")
                
                # 2. Se il nome contiene ancora sporcizia, resettiamolo
                if any(x in nuovo.upper() for x in ["REFERTO", "PARTITA", "PLV"]):
                    g['squadra'] = ""
                    count_cancellati += 1
                else:
                    g['squadra'] = nuovo
                    if vecchio != nuovo:
                        count_puliti += 1
        
        with open("giocatori.json", "w", encoding="utf-8") as f:
            json.dump(giocatori, f, indent=4, ensure_ascii=False)
            
        print(f">>> RIPARAZIONE COMPLETATA!")
        print(f"    - Squadre pulite dai simboli: {count_puliti}")
        print(f"    - Squadre errate resettate (saranno ri-assegnate): {count_cancellati}")
        print(">>> Ora rinomina questo file se necessario e aggiorna lo scraper.")
        
    except Exception as e:
        print(f"Errore: {e}")

if __name__ == "__main__":
    ripara_database_locale()