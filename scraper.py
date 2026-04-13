import os
import json
import requests
import re
from bs4 import BeautifulSoup
import firebase_admin
from firebase_admin import credentials, firestore

# --- 1. CONFIGURAZIONE ---
ID_CAMPIONATI = [39, 41, 42, 43] 

QUOTE_ROSA_FEM = [
    "FEDERICA FUNNONE", "MARTINA LUPO", "SABRINA CAPITOLO", "ARIANNA VISMARA", 
    "SABRINA ZANFRETTA", "SARA SOTTOLANO", "MARTINA BRACESCO", "ROSSELLA DE BLASIO", 
    "CARLOTTA AMODEO", "FEDERICA AMORELLI", "ELENA PASINO", "MARA FERRARIS", 
    "ALICE LA VERSA", "NOEMI CASTELLUCCIO", "CHIARA GILARDI"
]

HEADERS = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}

def inizializza_firebase():
    if not firebase_admin._apps:
        cred_json = os.environ.get('FIREBASE_SERVICE_ACCOUNT')
        if cred_json:
            cred = credentials.Certificate(json.loads(cred_json))
        else:
            cred = credentials.Certificate("serviceAccountKey.json")
        firebase_admin.initialize_app(cred)
    return firestore.client()

# --- LETTURA DATABASE LOCALE (IL TUO JSON) ---
def carica_anagrafica_locale(percorso_file="giocatori.json"):
    try:
        with open(percorso_file, 'r', encoding='utf-8') as f:
            dati = json.load(f)
        mappa = {}
        for g in dati:
            # Puliamo il nome esattamente come fa lo scraper per farli combaciare al 100%
            nome_clean = re.sub(r'[^A-Z\s\']', '', g['nome_reale'].upper()).strip()
            mappa[nome_clean] = {
                'categoria': g['categoria'],
                'prezzo': g['prezzo'],
                'nome_originale': g['nome_reale']
            }
        print(f">>> Caricati {len(mappa)} giocatori dal file {percorso_file}.")
        return mappa
    except Exception as e:
        print(f"ERRORE CRITICO: Impossibile leggere {percorso_file}. Dettagli: {e}")
        return {}

# --- 2. LOGICA CALCOLO PUNTI FANTAHITBALL ---
def calcola_punteggio_fanta(hits, autohits, fatti, subiti, giallo, rosso, is_fem, perso_tavolino):
    punti_base = (hits * 2) if is_fem else hits
    malus_autohit = -(autohits * 1)
    
    bonus_fatti = 5 if fatti >= 76 else (2 if fatti >= 51 else 0)
    
    if subiti <= 50: bonus_subiti = 5
    elif subiti <= 75: bonus_subiti = 2
    elif subiti >= 101: bonus_subiti = -5
    else: bonus_subiti = 0
    
    malus_disc = (-10 if giallo else 0) + (-20 if rosso else 0) + (-20 if perso_tavolino else 0)
    
    return punti_base + malus_autohit + bonus_fatti + bonus_subiti + malus_disc

# --- 3. ANALISI DEL SINGOLO REFERTO ---
def processa_referto(url, giornata, tot_casa, tot_trasf, db, mappa_giocatori):
    try:
        res = requests.get(url, headers=HEADERS, timeout=15)
        soup = BeautifulSoup(res.text, 'html.parser')
        
        tavolino_casa = False
        tavolino_trasf = False
        alert_tavolino = soup.find(string=re.compile(r'vinta a tavolino', re.I))
        if alert_tavolino:
            if tot_casa == 0: tavolino_casa = True
            if tot_trasf == 0: tavolino_trasf = True

        li_tags = soup.find_all('li', class_=re.compile(r'list-group-item', re.I))
        giocatori_match = []

        for li in li_tags:
            testo = li.get_text(separator=' ', strip=True)
            
            if "Tot." not in testo and "AUTOHIT" not in testo:
                continue

            nome_raw = re.split(r'\d+\s*x|Tot\.', testo, flags=re.I)[0].strip()
            nome_clean = re.sub(r'[^A-Z\s\']', '', nome_raw.upper()).strip()
            
            if len(nome_clean) < 3: 
                continue

            m_hits = re.search(r'Tot\.\s*(\d+)', testo, re.I)
            hits = int(m_hits.group(1)) if m_hits else 0
            
            m_auto = re.search(r'(\d+)\s*x\s*AUTOHIT', testo, re.I)
            autohits = int(m_auto.group(1)) if m_auto else 0

            giallo = li.find(class_=re.compile(r'warning|yellow', re.I)) is not None
            rosso = li.find(class_=re.compile(r'danger|red', re.I)) is not None

            giocatori_match.append({
                "nome": nome_clean, "hits": hits, "autohits": autohits, 
                "giallo": giallo, "rosso": rosso
            })

        mezzo = len(giocatori_match) / 2
        for idx, g in enumerate(giocatori_match):
            nome_db = g['nome']
            
            # SE IL GIOCATORE NON È NEL TUO JSON, LO IGNORA
            if nome_db not in mappa_giocatori:
                continue 

            doc_ref = db.collection('giocatori').document(nome_db)
            doc = doc_ref.get()
            dati_firebase = doc.to_dict() if doc.exists else {}
            
            # SE HA GIÀ I PUNTI DI QUESTA GIORNATA, SALTA
            if 'punti_giornate' in dati_firebase and str(giornata) in dati_firebase['punti_giornate']:
                continue 

            is_fem = nome_db in QUOTE_ROSA_FEM
            is_casa = (idx < mezzo)
            
            fatti = tot_casa if is_casa else tot_trasf
            subiti = tot_trasf if is_casa else tot_casa
            ha_perso_tavolino = (is_casa and tavolino_casa) or (not is_casa and tavolino_trasf)

            voto = calcola_punteggio_fanta(g['hits'], g['autohits'], fatti, subiti, g['giallo'], g['rosso'], is_fem, ha_perso_tavolino)
            
            # PREPARA I DATI DA SALVARE (Prende Categoria e Prezzo dal JSON)
            dati_da_salvare = {
                'nome': mappa_giocatori[nome_db]['nome_originale'],
                'categoria': mappa_giocatori[nome_db]['categoria'],
                'prezzo': mappa_giocatori[nome_db]['prezzo'],
                'punti_giornate': {str(giornata): voto}
            }
            
            doc_ref.set(dati_da_salvare, merge=True)
            print(f"  -> Aggiunto a Firebase: {nome_db} | {mappa_giocatori[nome_db]['categoria']} | (G{giornata}): {voto} pt")

    except Exception as e:
        print(f"Errore referto {url}: {e}")

# --- 4. CRAWLER ---
def recupera_e_analizza(db, mappa_giocatori):
    for camp_id in ID_CAMPIONATI:
        url_camp = f"https://referto.plvhitball.it/index.php?route=championship/championship/view&championship_id={camp_id}"
        print(f"\n{'='*40}\n>>> SCANSIONE CAMPIONATO ID: {camp_id}\n{'='*40}")
        
        try:
            res = requests.get(url_camp, headers=HEADERS, timeout=15)
            soup = BeautifulSoup(res.text, 'html.parser')
            
            for a_tag in soup.find_all('a', href=True):
                href = a_tag['href']
                
                if 'match_id=' in href or 'referto_id=' in href:
                    riga = a_tag
                    testo_riga = ""
                    for _ in range(5):
                        if riga.parent:
                            riga = riga.parent
                            testo_riga = riga.get_text(separator=' ', strip=True)
                            if re.search(r'Risultato:\s*\d+\s*-\s*\d+', testo_riga):
                                break
                    
                    match_ris = re.search(r'Risultato:\s*(\d+)\s*-\s*(\d+)', testo_riga)
                    if not match_ris:
                        continue 
                        
                    tot_casa = int(match_ris.group(1))
                    tot_trasf = int(match_ris.group(2))
                    
                    giornata = 1
                    curr = a_tag
                    while curr:
                        curr = curr.find_previous(['h1', 'h2', 'h3', 'h4', 'div', 'strong', 'b'])
                        if curr:
                            t = curr.get_text(strip=True)
                            m = re.search(r'(?:Giornata\s+(\d+))|(?:(\d+)[\^°a-z]*\s+Giornata)', t, re.IGNORECASE)
                            if m:
                                giornata = int(m.group(1) or m.group(2))
                                break
                    
                    url_ref = "https://referto.plvhitball.it/" + href.lstrip('/') if not href.startswith('http') else href
                    processa_referto(url_ref, giornata, tot_casa, tot_trasf, db, mappa_giocatori)
                    
        except Exception as e:
            print(f"Errore Campionato {camp_id}: {e}")

if __name__ == "__main__":
    print("Avvio FantaHitball Bot (Integrazione JSON Locale)...")
    mappa = carica_anagrafica_locale()
    
    if not mappa:
        print("Il bot si ferma perché non ha trovato o letto il file 'giocatori.json'.")
    else:
        db_firestore = inizializza_firebase()
        recupera_e_analizza(db_firestore, mappa)
        print("\n=== AGGIORNAMENTO FIREBASE COMPLETATO ===")
