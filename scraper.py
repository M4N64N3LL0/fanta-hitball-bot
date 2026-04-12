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
            # GitHub Actions
            config_dict = json.loads(cred_json)
            cred = credentials.Certificate(config_dict)
        else:
            # Locale
            cred = credentials.Certificate("serviceAccountKey.json")
        firebase_admin.initialize_app(cred)
    return firestore.client()

def calcola_punteggio_fanta(nome, hits, fatti, subiti, giallo, rosso, tavolino):
    # 1. Punti base: 1 hit = 1pt | FEM = 2pt
    is_fem = nome.upper().strip() in QUOTE_ROSA_FEM
    punti_base = (hits * 2) if is_fem else hits
    
    # 2. Bonus Squadra Segnati
    bonus_fatti = 0
    if fatti >= 76: bonus_fatti = 5
    elif fatti >= 51: bonus_fatti = 2
    
    # 3. Bonus/Malus Squadra Subiti
    bonus_subiti = 0
    if subiti <= 50: bonus_subiti = 5
    elif subiti <= 75: bonus_subiti = 2
    elif subiti >= 101: bonus_subiti = -5
    
    # 4. Malus Disciplinari (Dall'ispezione quadratini)
    malus_giallo = -10 if giallo else 0
    malus_rosso = -20 if rosso else 0
    malus_tavolino = -20 if tavolino else 0
    
    return punti_base + bonus_fatti + bonus_subiti + malus_giallo + malus_rosso + malus_tavolino

def analizza_partita(url, giornata, tavolino_casa=False, tavolino_trasferta=False):
    db = inizializza_firebase()
    try:
        res = requests.get(url, timeout=10)
        res.raise_for_status()
    except Exception as e:
        print(f"Errore connessione: {e}")
        return

    soup = BeautifulSoup(res.text, 'html.parser')
    tabelle = soup.find_all('table', class_='table-condensed')

    if len(tabelle) < 2:
        print("Errore: Non ho trovato le due tabelle delle squadre.")
        return

    # Estrazione Totali (Ultima riga delle tabelle)
    try:
        tot_casa = int(tabelle[0].find_all('tr')[-1].find_all('td')[2].text.strip())
        tot_trasferta = int(tabelle[1].find_all('tr')[-1].find_all('td')[2].text.strip())
    except (IndexError, ValueError) as e:
        print(f"Errore estrazione totali: {e}. Il referto potrebbe essere incompleto.")
        return

    print(f"--- ANALISI GIORNATA {giornata} ---")
    print(f"Risultato: {tot_casa} - {tot_trasferta}")

    for i, tabella in enumerate(tabelle[:2]):
        is_casa = (i == 0)
        fatti = tot_casa if is_casa else tot_trasferta
        subiti = tot_trasferta if is_casa else tot_casa
        tavolino = tavolino_casa if is_casa else tavolino_trasferta
        
        righe = tabella.find_all('tr')[1:-1] # Salto header e riga totali
        for riga in righe:
            cols = riga.find_all('td')
            if len(cols) < 3: continue
            
            nome = cols[1].text.strip().upper()
            try:
                hits = int(cols[2].text.strip())
            except: continue

            # Rilevamento Quadratini (Cartellini)
            # Cerco l'icona con classe text-warning (giallo) o text-danger (rosso)
            giallo = riga.find('i', class_='text-warning') is not None
            rosso = riga.find('i', class_='text-danger') is not None

            punti_finali = calcola_punteggio_fanta(nome, hits, fatti, subiti, giallo, rosso, tavolino)

            # Invio a Firebase
            db.collection('giocatori').document(nome).set({
                'punti_giornate': { str(giornata): punti_finali }
            }, merge=True)
            
            print(f"Aggiornato {nome}: {punti_finali} pt (Giallo: {giallo}, Rosso: {rosso})")

if __name__ == "__main__":
    # URL di esempio e Giornata
    URL_MERCATO = "INSERISCI_URL_QUI"
    GIORNATA_ATTUALE = 1
    analizza_partita(URL_MERCATO, GIORNATA_ATTUALE)
