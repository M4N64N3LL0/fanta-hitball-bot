import requests
from bs4 import BeautifulSoup
import firebase_admin
from firebase_admin import credentials, firestore
import os

# --- 1. CONFIGURAZIONE E LISTE ---
QUOTE_ROSA_FEM = [
    "FEDERICA FUNNONE", "MARTINA LUPO", "SABRINA CAPITOLO", "ARIANNA VISMARA", 
    "SABRINA ZANFRETTA", "SARA SOTTOLANO", "MARTINA BRACESCO", "ROSSELLA DE BLASIO", 
    "CARLOTTA AMODEO", "FEDERICA AMORELLI", "ELENA PASINO", "MARA FERRARIS", 
    "ALICE LA VERSA", "NOEMI CASTELLUCCIO", "CHIARA GILARDI"
]

def inizializza_firebase():
    if not firebase_admin._apps:
        # Cerca il segreto di GitHub o il file locale
        firebase_secret = os.environ.get('FIREBASE_SERVICE_ACCOUNT')
        if firebase_secret:
            with open("serviceAccountKey.json", "w") as f:
                f.write(firebase_secret)
        
        cred = credentials.Certificate("serviceAccountKey.json")
        firebase_admin.initialize_app(cred)
    return firestore.client()

# --- 2. LOGICA CALCOLO PUNTI (REGOLE UFFICIALI) ---
def calcola_punteggio_finale(nome, hits, fatti, subiti, giallo, rosso, tavolino):
    # Regola Base: 1 hit = 1pt | FEM = 2pt
    is_fem = nome.upper().strip() in QUOTE_ROSA_FEM
    punti_base = (hits * 2) if is_fem else hits
    
    # Bonus Squadra Segnati
    bonus_fatti = 0
    if fatti >= 76: bonus_fatti = 5
    elif fatti >= 51: bonus_fatti = 2
    
    # Bonus Squadra Subiti
    bonus_subiti = 0
    if subiti <= 50: bonus_subiti = 5
    elif subiti <= 75: bonus_subiti = 2
    elif subiti >= 101: bonus_subiti = -5
    
    # Malus Disciplinari (Quadratini)
    malus_giallo = -10 if giallo else 0
    malus_rosso = -20 if rosso else 0
    malus_tavolino = -20 if tavolino else 0
    
    return punti_base + bonus_fatti + bonus_subiti + malus_giallo + malus_rosso + malus_tavolino

# --- 3. SCRAPER E ANALISI REFERTO ---
def analizza_partita(url_referto, giornata, sconfitta_tavolino_casa=False, sconfitta_tavolino_trasferta=False):
    db = inizializza_firebase()
    print(f"Avvio Scraping Giornata {giornata}...")

    response = requests.get(url_referto)
    soup = BeautifulSoup(response.text, 'html.parser')

    # Trova le tabelle (solitamente table-condensed nel referto PLV)
    tabelle = soup.find_all('table', class_='table-condensed')
    if len(tabelle) < 2:
        print("Errore: tabelle non trovate. Verifica l'URL.")
        return

    # Estrazione Totale Punti Squadra (per calcolo subiti/fatti)
    # Di solito l'ultima riga della tabella contiene il totale
    totale_casa = int(tabelle[0].find_all('tr')[-1].find_all('td')[2].text.strip())
    totale_trasferta = int(tabelle[1].find_all('tr')[-1].find_all('td')[2].text.strip())

    for i, tabella in enumerate(tabelle):
        is_casa = (i == 0)
        fatti = totale_casa if is_casa else totale_trasferta
        subiti = totale_trasferta if is_casa else totale_casa
        tavolino = sconfitta_tavolino_casa if is_casa else sconfitta_tavolino_trasferta

        # Analisi righe giocatori
        righe = tabella.find_all('tr')[1:-1] # Salta intestazione e riga totale
        for riga in righe:
            cols = riga.find_all('td')
            if len(cols) < 3: continue
            
            nome = cols[1].text.strip().upper()
            try:
                hits = int(cols[2].text.strip())
            except ValueError: continue

            # --- Rilevamento Quadratini (Cartellini) ---
            # Cerchiamo l'icona <i class="fas fa-square text-warning">
            giallo = riga.find('i', class_='text-warning') is not None
            rosso = riga.find('i', class_='text-danger') is not None

            # Calcolo finale
            punti_finali = calcola_punteggio_finale(nome, hits, fatti, subiti, giallo, rosso, tavolino)

            # --- Aggiornamento Firebase ---
            doc_ref = db.collection('giocatori').document(nome)
            doc_ref.set({
                'punti_giornate': {
                    str(giornata): punti_finali
                }
            }, merge=True)

            print(f"Aggiornato: {nome} | {punti_finali} PT (Hits: {hits}, G: {giallo}, R: {rosso})")

# --- 4. ESECUZIONE ---
if __name__ == "__main__":
    # Cambia questi valori per ogni giornata
    URL = "https://referto.plvhitball.it/index.php?route=referto/referto&referto_id=XXXX"
    GIORNATA = 1
    analizza_partita(URL, GIORNATA)
