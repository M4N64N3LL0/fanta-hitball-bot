import os
import json
import requests
import re
from bs4 import BeautifulSoup
import firebase_admin
from firebase_admin import credentials, firestore

# --- 1. CONFIGURAZIONE ---
# ID esatti dei campionati PLV (A1, A2, B1, B2)
ID_CAMPIONATI = [39, 41, 42, 43] 

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
            config_dict = json.loads(cred_json)
            cred = credentials.Certificate(config_dict)
        else:
            cred = credentials.Certificate("serviceAccountKey.json")
        firebase_admin.initialize_app(cred)
    return firestore.client()

# --- 2. LOGICA CALCOLO PUNTI E REGOLE ---
def calcola_punteggio_fanta(nome, hits, fatti, subiti, giallo, rosso, tavolino, db):
    nome_up = nome.upper().strip()
    
    # Riconoscimento FEM (Lista manuale + Controllo Firebase)
    is_fem = nome_up in QUOTE_ROSA_FEM
    if not is_fem:
        doc = db.collection('giocatori').document(nome_up).get()
        if doc.exists and doc.to_dict().get('categoria') == "FEM":
            is_fem = True

    # Punti base
    punti_base = (hits * 2) if is_fem else hits
    
    # Bonus Squadra Segnati
    bonus_fatti = 5 if fatti >= 76 else (2 if fatti >= 51 else 0)
    
    # Bonus/Malus Squadra Subiti
    if subiti <= 50: bonus_subiti = 5
    elif subiti <= 75: bonus_subiti = 2
    elif subiti >= 101: bonus_subiti = -5
    else: bonus_subiti = 0
    
    # Malus Disciplinari
    malus = (-10 if giallo else 0) + (-20 if rosso else 0) + (-20 if tavolino else 0)
    
    return punti_base + bonus_fatti + bonus_subiti + malus

# --- 3. CRAWLER E ANALISI REFERTI ---
def recupera_e_analizza(db):
    for camp_id in ID_CAMPIONATI:
        url_camp = f"https://referto.plvhitball.it/index.php?route=championship/championship/view&championship_id={camp_id}"
        print(f"\n{'='*40}")
        print(f" >>> SCANSIONE CAMPIONATO ID: {camp_id}")
        print(f"{'='*40}")
        
        try:
            res = requests.get(url_camp, timeout=15)
            res.raise_for_status()
            soup = BeautifulSoup(res.text, 'html.parser')
            
            giornata_attuale = 1
            referti_trovati = 0
            
            # Scorre la pagina dall'alto verso il basso per associare le giornate ai referti
            for element in soup.find_all(['h1', 'h2', 'h3', 'h4', 'div', 'a']):
                testo = element.get_text(strip=True)
                
                # Rileva cambio giornata (es. "Giornata 1")
                m = re.search(r'Giornata\s+(\d+)', testo, re.IGNORECASE)
                if m and len(testo) < 30:
                    giornata_attuale = int(m.group(1))
                
                # Rileva link referto (Bottone azzurro)
                if element.name == 'a' and 'href' in element.attrs:
                    href = element['href']
                    if 'route=referto/referto&referto_id=' in href:
                        url_ref = "https://referto.plvhitball.it" + href if href.startswith('/') else href
                        
                        referti_trovati += 1
                        processa_referto(url_ref, giornata_attuale, db)
                        
            print(f"Completata analisi di {referti_trovati} referti per il Campionato {camp_id}.")
                    
        except Exception as e:
            print(f"Errore critico durante la scansione del campionato {camp_id}: {e}")

def processa_referto(url, giornata, db):
    try:
        res = requests.get(url, timeout=10)
        soup = BeautifulSoup(res.text, 'html.parser')
        tabelle = soup.find_all('table', class_='table-condensed')
        
        # Se non ci sono tabelle, la partita è SV (Senza Voto) o non giocata
        if len(tabelle) < 2: 
            return 

        # Estrazione Totali squadra (Ultima riga delle tabelle)
        try:
            tot_casa = int(tabelle[0].find_all('tr')[-1].find_all('td')[2].text.strip())
            tot_trasf = int(tabelle[1].find_all('tr')[-1].find_all('td')[2].text.strip())
        except (IndexError, ValueError):
            # Se la riga dei totali è vuota o sfasata, saltiamo
            return

        print(f"\n[G{giornata}] Analizzo referto: {tot_casa} - {tot_trasf}")

        for i, tabella in enumerate(tabelle[:2]):
            fatti = tot_casa if i == 0 else tot_trasf
            subiti = tot_trasf if i == 0 else tot_casa
            
            # Analisi giocatori (saltando header e totali)
            for riga in tabella.find_all('tr')[1:-1]:
                cols = riga.find_all('td')
                if len(cols) < 3: continue
                
                nome = cols[1].text.strip().upper()
                try: 
                    hits = int(cols[2].text.strip())
                except ValueError: 
                    continue

                # Rilevamento Cartellini
                giallo = riga.find('i', class_='text-warning') is not None
                rosso = riga.find('i', class_='text-danger') is not None

                # Calcolo punteggio FantaHitball
                voto = calcola_punteggio_fanta(nome, hits, fatti, subiti, giallo, rosso, False, db)

                # Aggiornamento su Firebase
                db.collection('giocatori').document(nome).set({
                    'punti_giornate': { str(giornata): voto }
                }, merge=True)
                
                cartellini_log = " [G]" if giallo else (" [R]" if rosso else "")
                print(f"  -> {nome}: {voto} pt{cartellini_log}")

    except requests.exceptions.RequestException as e:
        print(f"Errore di connessione al referto {url}: {e}")
    except Exception as e:
        print(f"Errore inaspettato sul referto {url}: {e}")

# --- 4. ESECUZIONE MAIN ---
if __name__ == "__main__":
    print("Avvio FantaHitball Bot...")
    db_firestore = inizializza_firebase()
    
    # Fa partire l'analisi di tutti i campionati
    recupera_e_analizza(db_firestore)
    
    print("\n=== AGGIORNAMENTO DI TUTTI I CAMPIONATI COMPLETATO ===")
