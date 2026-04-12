import os
import json
import requests
from bs4 import BeautifulSoup
import firebase_admin
from firebase_admin import credentials, firestore

# --- CONFIGURAZIONE ---
QUOTE_ROSA_FEM = [
    "FEDERICA FUNNONE", "MARTINA LUPO", "SABRINA CAPITOLO", "ARIANNA VISMARA", 
    "SABRINA ZANFRETTA", "SARA SOTTOLANO", "MARTINA BRACESCO", "ROSSELLA DE BLASIO", 
    "CARLOTTA AMODEO", "FEDERICA AMORELLI", "ELENA PASINO", "MARA FERRARIS", 
    "ALICE LA VERSA", "NOEMI CASTELLUCCIO", "CHIARA GILARDI"
]

def inizializza_firebase():
    if not firebase_admin._apps:
        cred_json = os.environ.get('FIREBASE_SERVICE_ACCOUNT')
        if cred_json:
            # Siamo su GitHub: creiamo il file dalle variabili d'ambiente
            with open("serviceAccountKey.json", "w") as f:
                f.write(cred_json)
            cred = credentials.Certificate("serviceAccountKey.json")
        else:
            # Siamo in locale: usiamo il file fisico
            cred = credentials.Certificate("serviceAccountKey.json")
        firebase_admin.initialize_app(cred)
    return firestore.client()

def calcola_voto(nome, hits, fatti, subiti, giallo, rosso, tavolino):
    # Regola base: 1 hit = 1pt | FEM = 2pt
    is_fem = nome.upper().strip() in QUOTE_ROSA_FEM
    punti_base = (hits * 2) if is_fem else hits
    
    # Bonus Squadra Segnati
    bonus_fatti = 5 if fatti >= 76 else (2 if fatti >= 51 else 0)
    
    # Bonus Squadra Subiti
    if subiti <= 50: bonus_subiti = 5
    elif subiti <= 75: bonus_subiti = 2
    elif subiti <= 100: bonus_subiti = 0
    else: bonus_subiti = -5 # 101+
    
    # Malus Disciplinari (Icone FontAwesome)
    malus = ( -10 if giallo else 0 ) + ( -20 if rosso else 0 ) + ( -20 if tavolino else 0 )
    
    return punti_base + bonus_fatti + bonus_subiti + malus

def analizza_partita(url, giornata):
    db = inizializza_firebase()
    res = requests.get(url)
    soup = BeautifulSoup(res.text, 'html.parser')

    # Trova tabelle referto
    tabelle = soup.find_all('table', class_='table-condensed')
    
    # Punteggi totali per bonus
    tot_casa = int(tabelle[0].find_all('tr')[-1].find_all('td')[2].text.strip())
    tot_trasf = int(tabelle[1].find_all('tr')[-1].find_all('td')[2].text.strip())

    for i, tabella in enumerate(tabelle):
        is_casa = (i == 0)
        fatti = tot_casa if is_casa else tot_trasf
        subiti = tot_trasf if is_casa else tot_casa
        
        righe = tabella.find_all('tr')[1:-1]
        for riga in righe:
            cols = riga.find_all('td')
            if len(cols) < 3: continue
            
            nome = cols[1].text.strip().upper()
            hits = int(cols[2].text.strip())
            
            # RILEVAMENTO QUADRATINI (fas fa-square)
            giallo = riga.find('i', class_='text-warning') is not None
            rosso = riga.find('i', class_='text-danger') is not None
            
            voto_finale = calcola_voto(nome, hits, fatti, subiti, giallo, rosso, False)

            # SALVATAGGIO SU FIREBASE
            db.collection('giocatori').document(nome).set({
                'punti_giornate': { str(giornata): voto_finale }
            }, merge=True)
            print(f"Aggiornato {nome}: {voto_finale} pt")

if __name__ == "__main__":
    # INSERISCI QUI L'URL E LA GIORNATA PRIMA DI FARE IL PUSH
    URL_REFERTO = "https://referto.plvhitball.it/index.php?route=referto/referto&referto_id=XXXX"
    GIORNATA = 1
    analizza_partita(URL_REFERTO, GIORNATA)
