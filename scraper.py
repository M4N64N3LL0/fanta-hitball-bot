import os
import json
import requests
import re
from bs4 import BeautifulSoup
import firebase_admin
from firebase_admin import credentials, firestore

ID_CAMPIONATI = [39, 41, 42, 43] 
HEADERS = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}

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
            cred = credentials.Certificate(json.loads(cred_json))
        else:
            cred = credentials.Certificate("serviceAccountKey.json")
        firebase_admin.initialize_app(cred)
    return firestore.client()

def carica_anagrafica_locale(percorso_file="giocatori.json"):
    try:
        with open(percorso_file, 'r', encoding='utf-8') as f:
            dati = json.load(f)
        mappa = {}
        for g in dati:
            n_raw = g['nome_reale'].upper()
            n_clean = re.sub(r'[^A-Z\s\']', '', n_raw).strip()
            mappa[n_clean] = {
                'nome_originale': g['nome_reale'],
                'categoria': g.get('categoria', 'MISTO'),
                'prezzo': g.get('prezzo', 0)
            }
        return mappa
    except Exception as e: 
        print(f"Errore caricamento JSON locale: {e}")
        return {}

# 1. NUOVA FUNZIONE: Scarica lo stato attuale di Firebase
def scarica_stato_firebase(db):
    print("\n>>> Lettura iniziale del database per ottimizzare le scritture...")
    docs = db.collection('giocatori').stream()
    stato = {}
    for doc in docs:
        stato[doc.id] = doc.to_dict()
    print(f">>> Trovati {len(stato)} giocatori attualmente nel database.\n")
    return stato

def calcola_punteggio_fanta(punti_tiri, autohits, fatti, subiti, giallo, rosso, is_fem, tav):
    p_base = (punti_tiri * 2) if is_fem else punti_tiri
    malus_auto = -(autohits * 1)
    bonus_att = 5 if fatti >= 76 else (2 if fatti >= 51 else 0)
    if subiti <= 50: bonus_def = 5
    elif subiti <= 75: bonus_def = 2
    elif subiti >= 101: bonus_def = -5
    else: bonus_def = 0
    malus_disc = (-10 if giallo else 0) + (-20 if rosso else 0) + (-20 if tav else 0)
    return p_base + malus_auto + bonus_att + bonus_def + malus_disc

def processa_referto(url, tot_casa, tot_trasf, db, mappa, stato_firebase, counter):
    try:
        res = requests.get(url, headers=HEADERS, timeout=15)
        soup = BeautifulSoup(res.text, 'html.parser')
        testo_pagina = soup.get_text(separator=' ')
        
        data_match = "0000-00-00"
        m_data = re.search(r'(\d{2})-(\d{2})-(\d{4})', testo_pagina)
        if m_data: data_match = f"{m_data.group(3)}-{m_data.group(2)}-{m_data.group(1)}"
        
        if data_match == "0000-00-00": return
        chiave_salvataggio = data_match 

        liste_squadre = soup.find_all('ul', class_=re.compile(r'list-group', re.I))
        if len(liste_squadre) < 2: return

        def estrai_e_salva(ul_node, is_casa):
            for li in ul_node.find_all('li', class_=re.compile(r'list-group-item', re.I)):
                testo = li.get_text(separator=' ', strip=True)
                if "x" not in testo: continue
                n_raw = re.split(r'\d+\s*x|Tot\.', testo, flags=re.I)[0].strip().upper()
                n_clean = re.sub(r'[^A-Z\s\']', '', n_raw).strip()
                
                if n_clean not in mappa: continue
                
                info_g = mappa[n_clean]
                
                punti_tiri = sum(int(q) * int(v) for q, v in re.findall(r'(\d+)\s*x\s*(2|3)', testo))
                m_auto = re.search(r'(\d+)\s*x\s*AUTOHIT', testo, re.I)
                autohits = int(m_auto.group(1)) if m_auto else 0
                giallo = li.find(class_=re.compile(r'warning|yellow', re.I)) is not None
                rosso = li.find(class_=re.compile(r'danger|red', re.I)) is not None
                
                fatti = tot_casa if is_casa else tot_trasf
                subiti = tot_trasf if is_casa else tot_casa
                tavolino = "tavolino" in testo_pagina.lower()
                tav_match = tavolino and ((is_casa and tot_casa == 0) or (not is_casa and tot_trasf == 0))
                
                voto = calcola_punteggio_fanta(punti_tiri, autohits, fatti, subiti, giallo, rosso, n_clean in QUOTE_ROSA_FEM, tav_match)
                
                # --- 2. IL CONTROLLO MAGICO (DIFFING) ---
                giocatore_fb = stato_firebase.get(n_clean, {})
                punti_fb = giocatore_fb.get('punti_giornate', {})
                
                # Controlliamo se qualcosa è cambiato rispetto a Firebase
                gia_presente = punti_fb.get(chiave_salvataggio) == voto
                stessa_cat = giocatore_fb.get('categoria') == info_g['categoria']
                stesso_prezzo = giocatore_fb.get('prezzo') == info_g['prezzo']

                # Se i dati combaciano perfettamente, non scriviamo nulla!
                if gia_presente and stessa_cat and stesso_prezzo:
                    counter['risparmiate'] += 1
                    print(f"      [SKIP] {n_clean} | Voto già presente, scrittura risparmiata.")
                else:
                    # I dati sono nuovi o aggiornati: Prepariamo la scrittura
                    ref = db.collection('giocatori').document(n_clean)
                    counter['batch'].set(ref, {
                        'nome_reale': info_g['nome_originale'],
                        'categoria': info_g['categoria'],
                        'prezzo': info_g['prezzo'],
                        'punti_giornate': { chiave_salvataggio: voto }
                    }, merge=True)
                    
                    counter['effettuate'] += 1
                    print(f"      [UPDATE] {n_clean} | {chiave_salvataggio} | {voto}pt")

                    # Aggiorniamo la memoria locale così se incontra di nuovo lo stesso giocatore non lo riscrive
                    if n_clean not in stato_firebase:
                        stato_firebase[n_clean] = {'punti_giornate': {}}
                    stato_firebase[n_clean]['punti_giornate'][chiave_salvataggio] = voto
                    stato_firebase[n_clean]['categoria'] = info_g['categoria']
                    stato_firebase[n_clean]['prezzo'] = info_g['prezzo']

                    # Firebase accetta massimo 500 scritture per batch. A 400 inviamo il pacco e ne apriamo uno nuovo.
                    if counter['effettuate'] % 400 == 0:
                        counter['batch'].commit()
                        counter['batch'] = db.batch()
                        print("      >>> Inviato blocco intermedio a Firebase...")

        estrai_e_salva(liste_squadre[0], True)
        estrai_e_salva(liste_squadre[1], False)
    except Exception as e: print(f"      [ERR] {e}")

def recupera_e_analizza(db, mappa):
    # Inizializziamo lo stato e i contatori
    stato_firebase = scarica_stato_firebase(db)
    counter = {
        'batch': db.batch(),
        'risparmiate': 0,
        'effettuate': 0
    }

    for camp_id in ID_CAMPIONATI:
        url_camp = f"https://referto.plvhitball.it/index.php?route=championship/championship/view&championship_id={camp_id}"
        print(f"\n>>> SCANSIONE CAMPIONATO {camp_id}")
        try:
            res = requests.get(url_camp, headers=HEADERS, timeout=15)
            soup = BeautifulSoup(res.text, 'html.parser')
            for a_tag in soup.find_all('a', href=True):
                if 'match_id=' in a_tag['href'] or 'referto_id=' in a_tag['href']:
                    riga = a_tag
                    for _ in range(5):
                        if riga.parent:
                            riga = riga.parent
                            if "Risultato:" in riga.get_text(): break
                    m_ris = re.search(r'Risultato:\s*(\d+)\s*-\s*(\d+)', riga.get_text())
                    if m_ris:
                        processa_referto("https://referto.plvhitball.it/" + a_tag['href'].lstrip('/'), int(m_ris.group(1)), int(m_ris.group(2)), db, mappa, stato_firebase, counter)
        except Exception as e: print(f">>> Errore: {e}")

    # Commit finale per inviare le ultime scritture rimaste nel batch
    if counter['effettuate'] % 400 != 0 and counter['effettuate'] > 0:
        counter['batch'].commit()

    # Stampiamo il bollettino della vittoria
    print(f"\n=========================================")
    print(f">>> AGGIORNAMENTO COMPLETATO!")
    print(f">>> Scritture Inviate su Firebase: {counter['effettuate']}")
    print(f">>> Scritture Risparmiate:         {counter['risparmiate']}")
    print(f"=========================================\n")

if __name__ == "__main__":
    mappa_g = carica_anagrafica_locale("giocatori.json")
    if mappa_g:
        db_fs = inizializza_firebase()
        recupera_e_analizza(db_fs, mappa_g)
