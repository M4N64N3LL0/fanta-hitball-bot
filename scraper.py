import requests
from bs4 import BeautifulSoup
import firebase_admin
from firebase_admin import credentials, firestore
import os

# --- LOGICA CALCOLO PUNTI SECONDO LE TUE REGOLE ---
def calcola_fanta_punti(nome, hits, subiti, fatti, giallo, rosso, tavolino, is_fem):
    # 1. Punti base (1 hit = 1pt, FEM = 2pt)
    punti_base = (hits * 2) if is_fem else hits
    
    # 2. Bonus Punti Squadra Segnati
    bonus_fatti = 0
    if fatti >= 76: bonus_fatti = 5
    elif fatti >= 51: bonus_fatti = 2
    
    # 3. Bonus/Malus Punti Squadra Subiti
    bonus_subiti = 0
    if subiti <= 50: bonus_subiti = 5
    elif subiti <= 75: bonus_subiti = 2
    elif subiti >= 101: bonus_subiti = -5
    
    # 4. Malus Disciplinari e Tavolino
    malus_giallo = -10 if giallo else 0
    malus_rosso = -20 if rosso else 0
    malus_tavolino = -20 if tavolino else 0
    
    totale = punti_base + bonus_fatti + bonus_subiti + malus_giallo + malus_rosso + malus_tavolino
    return totale

def aggiorna_database(url_referto, giornata, is_tavolino=False):
    # (Codice di scraping per estrarre i dati dal referto PLV)
    # Supponiamo di aver estratto questi dati per un giocatore:
    nome_giocatore = "MARCO ROSSI"
    hits_fatti = 20
    punti_squadra_fatti = 80
    punti_squadra_subiti = 45
    ha_giallo = True  # Trovato quadratino giallo nel referto
    ha_rosso = False
    
    # Controllo se è FEM (dalla nostra lista o dal DB)
    is_fem = nome_giocatore in QUOTE_ROSA_FEM 
    
    punteggio_finale = calcola_fanta_punti(
        nome_giocatore, 
        hits_fatti, 
        punti_squadra_subiti, 
        punti_squadra_fatti, 
        ha_giallo, 
        ha_rosso, 
        is_tavolino, 
        is_fem
    )
    
    # Invio a Firebase
    db.collection('giocatori').document(nome_giocatore).set({
        'punti_giornate': { str(giornata): punteggio_finale }
    }, merge=True)
    
    print(f"Aggiornato {nome_giocatore}: {punteggio_finale} PT")

# --- LISTA QUOTE ROSA (PER IL RADDOPPIO) ---
QUOTE_ROSA_FEM = [
    "FEDERICA FUNNONE", "MARTINA LUPO", "SABRINA CAPITOLO", "ARIANNA VISMARA", 
    "SABRINA ZANFRETTA", "SARA SOTTOLANO", "MARTINA BRACESCO", "ROSSELLA DE BLASIO", 
    "CARLOTTA AMODEO", "FEDERICA AMORELLI", "ELENA PASINO", "MARA FERRARIS", 
    "ALICE LA VERSA", "NOEMI CASTELLUCCIO", "CHIARA GILARDI"
]
