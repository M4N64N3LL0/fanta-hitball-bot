import os
import time
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

BLACKLIST_NOMI = [
    "COPYRIGHT", "COMPORTAMENTO", "SCORRETTO", "ANTISPORTIVO", 
    "SANZIONI", "DISCIPLINARI", "MINUTO", "AMMONIZIONE", 
    "ESPULSIONE", "SQUADRA", "DIRIGENTE", "ALLENATORE",
    "DIVISA", "GIOCO", "REGOLAMENTARE", "RITARDO", "AUTOHIT"
]

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
}

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

# --- 2. LOGICA PUNTI ---
def calcola_punteggio_fanta(nome, hits, autohits, fatti, subiti, giallo, rosso, db):
    nome_up = nome.upper().strip()
    is_fem = nome_up in QUOTE_ROSA_FEM
    if not is_fem:
        doc = db.collection('giocatori').document(nome_up).get()
        if doc.exists and doc.to_dict().get('categoria') == "FEM":
            is_fem = True

    punti_base = (hits * 2) if is_fem else hits
    malus_autohit = -(autohits * 1)
    bonus_fatti = 5 if fatti >= 76 else (2 if fatti >= 51 else 0)
    
    if subiti <= 50: bonus_subiti = 5
    elif subiti <= 75: bonus_subiti = 2
    elif subiti >= 101: bonus_subiti = -5
    else: bonus_subiti = 0
    
    malus_disc = (-10 if giallo else 0) + (-20 if rosso else 0)
    return punti_base + malus_autohit + bonus_fatti + bonus_subiti + malus_disc

# --- 3. ANALISI REFERTO ---
def processa_referto(url, giornata, tot_casa, tot_trasf, db, session):
    try:
        time.sleep(1)
        res = session.get(url, timeout=10)
        soup = BeautifulSoup(res.text, 'html.parser')
        
        # Cerchiamo tutti i blocchi che contengono la scritta "Tot."
        labels_punti = soup.find_all(string=re.compile(r'Tot\.', re.I))
        giocatori_match = []

        for label in labels_punti:
            # Risaliamo al contenitore del singolo giocatore
            container = label.find_parent('div', class_=re.compile(r'col|row|block|item', re.I))
            if not container: continue
            
            testo = container.get_text(separator=' ', strip=True)
            # Pulizia per estrarre il nome
            nome_raw = re.split(r'Tot\.', testo, flags=re.I)[0].strip().upper()
            nome_clean = re.sub(r'[^A-Z\s]', '', nome_raw).strip()

            if len(nome_clean) < 5 or any(x in nome_clean for x in BLACKLIST_NOMI):
                continue

            # Estrazione Hits e Autohits
            m_hits = re.search(r'Tot\.\s*(\d+)', testo, re.I)
            hits = int(m_hits.group(1)) if m_hits else 0
            
            m_auto = re.search(r'(\d+)\s*x\s*AUTOHIT', testo, re.I)
            autohits = int(m_auto.group(1)) if m_auto else 0

            # Cartellini nel "recinto" del giocatore
            giallo = container.find(class_=re.compile(r'warning|yellow', re.I)) is not None
            rosso = container.find(class_=re.compile(r'danger|red', re.I)) is not None

            if nome_clean not in [g['nome'] for g in giocatori_match]:
                giocatori_match.append({
                    "nome": nome_clean, "hits": hits, "autohits": autohits, 
                    "giallo": giallo, "rosso": rosso
                })

        # Suddivisione Casa/Trasferta (50/50 dell'elenco apparizione)
        mezzo = len(giocatori_match) // 2
        for idx, g in enumerate(giocatori_match):
            f, s = (tot_casa, tot_trasf) if idx < mezzo else (tot_trasf, tot_casa)
            
            # --- CONTROLLO FIREBASE: SALTA SE GIÀ PRESENTE ---
            doc_ref = db.collection('giocatori').document(g['nome'])
            doc = doc_ref.get()
            esiste = False
            if doc.exists:
                dati = doc.to_dict()
                if 'punti_giornate' in dati and str(giornata) in dati['punti_giornate']:
                    esiste = True
            
            if esiste:
                continue # Salta il calcolo e la scrittura se il voto c'è già

            voto = calcola_punteggio_fanta(g['nome'], g['hits'], g['autohits'], f, s, g['giallo'], g['rosso'], db)
            doc_ref.set({'punti_giornate': {str(giornata): voto}}, merge=True)
            
            c_log = " [GIALLO]" if g['giallo'] else (" [ROSSO]" if g['rosso'] else "")
            print(f"  -> {g['nome']} (G{giornata}): {voto} pt (H:{g['hits']} A:{g['autohits']}){c_log}")

    except Exception as e:
        print(f"Errore referto {url}: {e}")

# --- 4. CRAWLER PRINCIPALE ---
def recupera_e_analizza(db):
    session = requests.Session()
    session.headers.update(HEADERS)

    for camp_id in ID_CAMPIONATI:
        url_camp = f"https://referto.plvhitball.it/index.php?route=championship/championship/view&championship_id={camp_id}"
        print(f"\n>>> SCANSIONE CAMPIONATO ID: {camp_id}")
        
        try:
            res = session.get(url_camp, timeout=15)
            soup = BeautifulSoup(res.text, 'html.parser')
            
            for a_tag in soup.find_all('a', href=True):
                if 'route=match/result' in a_tag['href']:
                    href = a_tag['href']
                    # Trova Risultato e Giornata
                    riga = a_tag.find_parent('div') or a_tag.find_parent('td')
                    testo_riga = riga.get_text(separator=' ', strip=True) if riga else ""
                    
                    match_ris = re.search(r'Risultato:\s*(\d+)\s*-\s*(\d+)', testo_riga)
                    if not match_ris: continue
                    
                    tot_casa, tot_trasf = int(match_ris.group(1)), int(match_ris.group(2))
                    
                    giornata = 1
                    curr = a_tag
                    while curr:
                        curr = curr.find_previous(['h1', 'h2', 'h3', 'h4', 'div'])
                        if curr:
                            t = curr.get_text(strip=True)
                            m = re.search(r'(?:Giornata\s+(\d+))|(?:(\d+)[\^°a-z]*\s+Giornata)', t, re.IGNORECASE)
                            if m:
                                giornata = int(m.group(1) or m.group(2))
                                break

                    url_match = "https://referto.plvhitball.it/" + href.lstrip('/') if not href.startswith('http') else href
                    processa_referto(url_match, giornata, tot_casa, tot_trasf, db, session)
                    
        except Exception as e:
            print(f"Errore Campionato {camp_id}: {e}")

if __name__ == "__main__":
    print("Avvio FantaHitball Bot Totale...")
    db_firestore = inizializza_firebase()
    recupera_e_analizza(db_firestore)
    print("\n=== AGGIORNAMENTO COMPLETATO ===")
