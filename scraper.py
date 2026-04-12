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

# BLACKLIST: Tutte le parole che il sito usa per i "falsi giocatori" o penalità
BLACKLIST_NOMI = [
    "COPYRIGHT", "COMPORTAMENTO", "SCORRETTO", "ANTISPORTIVO", 
    "SANZIONI", "DISCIPLINARI", "MINUTO", "AMMONIZIONE", 
    "ESPULSIONE", "SQUADRA", "DIRIGENTE", "ALLENATORE",
    "DIVISA", "GIOCO", "REGOLAMENTARE", "RITARDO"
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

# --- 2. LOGICA PUNTI FANTAHITBALL ---
def calcola_punteggio_fanta(nome, hits, autohits, fatti, subiti, giallo, rosso, tavolino, db):
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
    
    malus_disciplinari = (-10 if giallo else 0) + (-20 if rosso else 0) + (-20 if tavolino else 0)
    
    return punti_base + malus_autohit + bonus_fatti + bonus_subiti + malus_disciplinari

# --- 3. CRAWLER E ANALISI NUOVO LAYOUT ---
def recupera_e_analizza(db):
    session = requests.Session()
    session.headers.update(HEADERS)

    for camp_id in ID_CAMPIONATI:
        url_camp = f"https://referto.plvhitball.it/index.php?route=championship/championship/view&championship_id={camp_id}"
        print(f"\n{'='*50}\n >>> SCANSIONE CAMPIONATO ID: {camp_id}\n{'='*50}")
        
        try:
            res = session.get(url_camp, timeout=15)
            if res.status_code != 200: continue

            soup = BeautifulSoup(res.text, 'html.parser')
            referti_processati = set()
            
            for a_tag in soup.find_all('a', href=True):
                href = a_tag['href']
                
                if 'route=match/result' in href:
                    match_id = re.search(r'match_id=(\d+)', href).group(1)
                    if match_id in referti_processati: continue
                    referti_processati.add(match_id)
                    
                    testo_riga = ""
                    curr_parent = a_tag
                    for _ in range(3): 
                        curr_parent = curr_parent.parent
                        if curr_parent:
                            testo_riga = curr_parent.get_text(separator=' ', strip=True)
                            if "Risultato:" in testo_riga: break
                            
                    match_ris = re.search(r'Risultato:\s*(\d+)\s*-\s*(\d+)', testo_riga)
                    if not match_ris:
                        continue 
                        
                    tot_casa = int(match_ris.group(1))
                    tot_trasf = int(match_ris.group(2))
                    
                    giornata_attuale = 1
                    curr = a_tag
                    while curr:
                        curr = curr.find_previous(['h1', 'h2', 'h3', 'h4', 'div'])
                        if curr:
                            t = curr.get_text(strip=True)
                            m = re.search(r'(?:Giornata\s+(\d+))|(?:(\d+)[\^°a-z]*\s+Giornata)', t, re.IGNORECASE)
                            if m:
                                giornata_attuale = int(m.group(1) or m.group(2))
                                break
                    
                    url_match = "https://referto.plvhitball.it/" + href.lstrip('/') if not href.startswith('http') else href
                    processa_referto_nuovo_layout(url_match, giornata_attuale, tot_casa, tot_trasf, db, session)
                    
        except Exception as e:
            print(f"[ERRORE] Campionato {camp_id}: {e}")

def processa_referto_nuovo_layout(url, giornata, tot_casa, tot_trasf, db, session):
    try:
        time.sleep(0.5) 
        res = session.get(url, timeout=10)
        soup = BeautifulSoup(res.text, 'html.parser')
        
        print(f"\n[G{giornata}] Analizzo Match: {tot_casa} - {tot_trasf}")
        
        testo_pulito = list(soup.stripped_strings)
        giocatori_estratti = []
        
        for i, token in enumerate(testo_pulito):
            if token.upper() == "TOT.":
                if i < 2 or i > len(testo_pulito) - 3: continue
                
                hits_str = testo_pulito[i+1]
                if not hits_str.isdigit(): continue
                hits = int(hits_str)
                
                prev_token = testo_pulito[i-1].upper()
                nome = ""
                autohits = 0
                
                # SQUADRA IN TRASFERTA
                if "AUTOHIT" in prev_token:
                    nome = testo_pulito[i+2].upper()
                    num_match = re.search(r'(\d+)\s*x\s*AUTOHIT', prev_token)
                    if num_match: autohits = int(num_match.group(1))
                    elif testo_pulito[i-2].isdigit(): autohits = int(testo_pulito[i-2])
                        
                # SQUADRA DI CASA
                else:
                    nome = prev_token.upper()
                    for j in range(i+1, min(i+12, len(testo_pulito))):
                        if "AUTOHIT" in testo_pulito[j].upper():
                            num_match = re.search(r'(\d+)\s*x\s*AUTOHIT', testo_pulito[j].upper())
                            if num_match: autohits = int(num_match.group(1))
                            elif testo_pulito[j-1].isdigit(): autohits = int(testo_pulito[j-1])
                            break
                            
                # FILTRO BLACKLIST RIGOROSO
                nome_sicuro = True
                if not re.search(r'[A-Za-z]', nome): 
                    nome_sicuro = False
                else:
                    for parola in BLACKLIST_NOMI:
                        if parola in nome:
                            nome_sicuro = False
                            break
                
                if not nome_sicuro: continue 
                
                giocatori_estratti.append({"nome": nome, "hits": hits, "autohits": autohits})

        mezzo = len(giocatori_estratti) // 2
        for idx, gio in enumerate(giocatori_estratti):
            is_casa = idx < mezzo
            fatti = tot_casa if is_casa else tot_trasf
            subiti = tot_trasf if is_casa else tot_casa
            
            # IL "RECINTO" PER I CARTELLINI
            giallo, rosso = False, False
            # Trova esattamente il nome ignorando spazi
            nodi_nome = soup.find_all(string=lambda t: t and gio['nome'] in t.upper())
            for nodo in nodi_nome:
                genitore = nodo.parent
                for _ in range(5): # Sale nell'HTML per creare il blocco
                    if not genitore or genitore.name == 'body': break
                    
                    # Sicurezza: Se il blocco contiene più di 20 testi, è diventato troppo grande (es. tutta la squadra)
                    if len(list(genitore.stripped_strings)) > 20: break

                    # Cerca l'icona nel recinto del giocatore
                    if genitore.find(class_=re.compile(r'warning|yellow', re.I)): giallo = True
                    if genitore.find(class_=re.compile(r'danger|red', re.I)): rosso = True
                    
                    if giallo or rosso: break
                    genitore = genitore.parent
                if giallo or rosso: break # Trovato, smetti di cercare

            voto = calcola_punteggio_fanta(gio['nome'], gio['hits'], gio['autohits'], fatti, subiti, giallo, rosso, False, db)

            db.collection('giocatori').document(gio['nome']).set({
                'punti_giornate': { str(giornata): voto }
            }, merge=True)
            
            cart_log = " [GIALLO]" if giallo else (" [ROSSO]" if rosso else "")
            print(f"  -> {gio['nome']}: {voto} pt (Hits: {gio['hits']}, Autohits: {gio['autohits']}){cart_log}")

    except Exception as e:
        print(f"[ERRORE] Referto {url}: {e}")

if __name__ == "__main__":
    print("Avvio FantaHitball Bot (Recinto Cartellini Attivato)...")
    db_firestore = inizializza_firebase()
    recupera_e_analizza(db_firestore)
    print("\n=== AGGIORNAMENTO DI TUTTI I CAMPIONATI COMPLETATO ===")
