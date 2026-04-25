import os
import sys
import json
import requests
import re
from bs4 import BeautifulSoup
import firebase_admin
from firebase_admin import credentials, firestore

try:
    sys.stdout.reconfigure(line_buffering=True)
except AttributeError:
    pass

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
            nome_originale = g['nome_reale'].upper()
            
            # FIX: Accettiamo A-Z, accenti, spazi E APOSTROFI (\')
            nome_clean = re.sub(r'[^A-ZÀÈÉÌÒÙÁÍÓÚ\'\s]', ' ', nome_originale).strip()
            parole = frozenset(nome_clean.split())
            
            mappa[parole] = {
                'id_documento': nome_originale,
                'nome_display': g['nome_reale'],
                'categoria': g.get('categoria', 'MISTO'),
                'prezzo': g.get('prezzo', 0),
                'squadra': g.get('squadra', '')
            }
        return mappa
    except Exception as e: 
        print(f"Errore caricamento JSON: {e}", flush=True)
        return {}

def scarica_stato_firebase(db):
    print("\n>>> Lettura database per ottimizzazione...", flush=True)
    docs = db.collection('giocatori').stream()
    return {doc.id: doc.to_dict() for doc in docs}

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

def processa_referto(url, tot_casa, tot_trasf, db, mappa, stato_fb, counter):
    try:
        res = requests.get(url, headers=HEADERS, timeout=15)
        soup = BeautifulSoup(res.text, 'html.parser')
        testo_pagina = soup.get_text(separator=' ')
        
        m_data = re.search(r'(\d{2})-(\d{2})-(\d{4})', testo_pagina)
        if not m_data: return
        data_match = f"{m_data.group(3)}-{m_data.group(2)}-{m_data.group(1)}"

        liste_squadre = soup.find_all('ul', class_=re.compile(r'list-group', re.I))
        if len(liste_squadre) < 2: return

        def estrai_e_salva(ul_node, is_casa):
            for li in ul_node.find_all('li', class_=re.compile(r'list-group-item', re.I)):
                testo_originale = li.get_text(separator=' ', strip=True)
                
                # FIX: Uniformiamo tutti i tipi di apostrofo strani del sito web in quello standard
                testo_upper = testo_originale.upper().replace('’', "'").replace('‘', "'").replace('`', "'")
                
                # FIX: Stessa logica con A-Z, accenti, spazi e apostrofi
                testo_clean = re.sub(r'[^A-ZÀÈÉÌÒÙÁÍÓÚ\'\s]', ' ', testo_upper)
                parole_nella_riga = testo_clean.split()

                id_fb = None
                info_g = None

                for parole_json, info in mappa.items():
                    # Ora "D'URZO" cercherà esattamente la parola intera "D'URZO" nel testo web
                    if all(p in parole_nella_riga for p in parole_json):
                        id_fb = info['id_documento']
                        info_g = info
                        break
                
                if not id_fb:
                    if "PENALIT" not in testo_originale.upper() and "MANCATA" not in testo_originale.upper():
                        print(f"      [SKIP] Nessuno dei tuoi giocatori è qui: '{testo_originale}'", flush=True)
                    continue

                punti_tiri = sum(int(q) * int(v) for q, v in re.findall(r'(\d+)\s*x\s*(2|3)', testo_originale, re.I))
                if punti_tiri == 0:
                    m_tot = re.search(r'Tot\.\s*(\d+)', testo_originale, re.I)
                    if m_tot: punti_tiri = int(m_tot.group(1))

                m_auto = re.search(r'(\d+)\s*x\s*AUTOHIT', testo_originale, re.I)
                autohits = int(m_auto.group(1)) if m_auto else 0
                giallo = li.find(class_=re.compile(r'warning|yellow', re.I)) is not None
                rosso = li.find(class_=re.compile(r'danger|red', re.I)) is not None
                
                fatti = tot_casa if is_casa else tot_trasf
                subiti = tot_trasf if is_casa else tot_casa
                tavolino = "tavolino" in testo_pagina.lower()
                tav_match = tavolino and ((is_casa and tot_casa == 0) or (not is_casa and tot_trasf == 0))
                
                voto = calcola_punteggio_fanta(punti_tiri, autohits, fatti, subiti, giallo, rosso, id_fb in QUOTE_ROSA_FEM, tav_match)
                
                fb_data = stato_fb.get(id_fb, {})
                voto_esistente = fb_data.get('punti_giornate', {}).get(data_match)
                
                if voto_esistente == voto:
                    counter['risparmiate'] += 1
                    print(f"      [GIA' PRESENTE] {id_fb} ha già {voto}pt per questa data, salto.", flush=True)
                else:
                    db.collection('giocatori').document(id_fb).set({
                        'nome_reale': info_g['nome_display'],
                        'categoria': info_g['categoria'],
                        'prezzo': info_g['prezzo'],
                        'squadra': info_g['squadra'],
                        'is_fem': id_fb in QUOTE_ROSA_FEM,
                        'punti_giornate': { data_match: voto }
                    }, merge=True)
                    print(f"      [UPDATE] Trovato e aggiornato: {id_fb} | {voto}pt", flush=True)
                    counter['effettuate'] += 1

        estrai_e_salva(liste_squadre[0], True)
        estrai_e_salva(liste_squadre[1], False)
    except Exception as e: print(f"      [ERR] Referto: {e}", flush=True)

def recupera_e_analizza(db, mappa):
    stato_fb = scarica_stato_firebase(db)
    counter = {'effettuate': 0, 'risparmiate': 0}
    for camp_id in ID_CAMPIONATI:
        url_camp = f"https://referto.plvhitball.it/index.php?route=championship/championship/view&championship_id={camp_id}"
        print(f"\n>>> ANALISI CAMPIONATO {camp_id}", flush=True)
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
                        processa_referto("https://referto.plvhitball.it/" + a_tag['href'].lstrip('/'), int(m_ris.group(1)), int(m_ris.group(2)), db, mappa, stato_fb, counter)
        except Exception as e: print(f">>> Errore: {e}", flush=True)
    print(f"\n>>> FINE. Scritture: {counter['effettuate']} | Risparmiate: {counter['risparmiate']}", flush=True)

if __name__ == "__main__":
    mappa_g = carica_anagrafica_locale("giocatori.json")
    if mappa_g:
        db_fs = inizializza_firebase()
        recupera_e_analizza(db_fs, mappa_g)
