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
            mappa[n_clean] = {'nome_originale': g['nome_reale']}
        return mappa
    except: return {}

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

def processa_referto(url, tot_casa, tot_trasf, db, mappa):
    try:
        res = requests.get(url, headers=HEADERS, timeout=15)
        soup = BeautifulSoup(res.text, 'html.parser')
        
        # --- TATTICA BULLDOZER: DISTRUGGIAMO I MENU ---
        # Creiamo una copia della pagina per estrarre Data e Giornata in sicurezza
        soup_testo = BeautifulSoup(res.text, 'html.parser')
        # Cancelliamo header, barre laterali e soprattutto TUTTI I LINK (<a>)
        for tag_inutile in soup_testo.find_all(['nav', 'aside', 'header', 'footer', 'a', 'button']):
            tag_inutile.decompose() 
            
        testo_pagina_pulito = soup_testo.get_text(separator=' ')
        
        # 1. ESTRAZIONE DATA
        data_match = "0000-00-00"
        m_data = re.search(r'(\d{2})-(\d{2})-(\d{4})', testo_pagina_pulito)
        if m_data: 
            data_match = f"{m_data.group(3)}-{m_data.group(2)}-{m_data.group(1)}"
        
        # 2. ESTRAZIONE GIORNATA
        giornata = 0
        mg = re.search(r'(\d+)[\^°\s]*Giornata|Giornata\s+(\d+)|G\.\s*(\d+)', testo_pagina_pulito, re.I)
        if mg:
            giornata = int(next(g for g in mg.groups() if g is not None))

        # Se non trova la giornata o la data, ignora il referto
        if data_match == "0000-00-00" or giornata == 0: 
            print("      [SKIP] Impossibile trovare Data o Giornata valide in questa pagina.")
            return

        # LA MAGIA: CHIAVE COMPOSTA (es: "2026-04-19_17")
        chiave_salvataggio = f"{data_match}_{giornata}"

        # Usiamo il 'soup' originale (non distrutto) per leggere i giocatori
        liste_squadre = soup.find_all('ul', class_=re.compile(r'list-group', re.I))
        if len(liste_squadre) < 2: return

        def estrai_e_salva(ul_node, is_casa):
            for li in ul_node.find_all('li', class_=re.compile(r'list-group-item', re.I)):
                testo = li.get_text(separator=' ', strip=True)
                if "x" not in testo: continue
                n_raw = re.split(r'\d+\s*x|Tot\.', testo, flags=re.I)[0].strip().upper()
                n_clean = re.sub(r'[^A-Z\s\']', '', n_raw).strip()
                if n_clean not in mappa: continue
                
                punti_tiri = sum(int(q) * int(v) for q, v in re.findall(r'(\d+)\s*x\s*(2|3)', testo))
                m_auto = re.search(r'(\d+)\s*x\s*AUTOHIT', testo, re.I)
                autohits = int(m_auto.group(1)) if m_auto else 0
                giallo = li.find(class_=re.compile(r'warning|yellow', re.I)) is not None
                rosso = li.find(class_=re.compile(r'danger|red', re.I)) is not None
                
                fatti = tot_casa if is_casa else tot_trasf
                subiti = tot_trasf if is_casa else tot_casa
                tavolino = "tavolino" in testo_pagina_pulito.lower()
                tav_match = tavolino and ((is_casa and tot_casa == 0) or (not is_casa and tot_trasf == 0))
                
                voto = calcola_punteggio_fanta(punti_tiri, autohits, fatti, subiti, giallo, rosso, n_clean in QUOTE_ROSA_FEM, tav_match)
                
                db.collection('giocatori').document(n_clean).set({
                    'punti_giornate': { chiave_salvataggio: voto }
                }, merge=True)
                
                print(f"      [OK] {n_clean} | G{giornata} ({data_match}): {voto}pt")

        estrai_e_salva(liste_squadre[0], True)
        estrai_e_salva(liste_squadre[1], False)
    except Exception as e: print(f"      [ERR] {e}")

def recupera_e_analizza(db, mappa):
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
                        processa_referto("https://referto.plvhitball.it/" + a_tag['href'].lstrip('/'), int(m_ris.group(1)), int(m_ris.group(2)), db, mappa)
        except Exception as e: print(f">>> Errore: {e}")

if __name__ == "__main__":
    mappa_g = carica_anagrafica_locale()
    if mappa_g:
        db_fs = inizializza_firebase()
        recupera_e_analizza(db_fs, mappa_g)
        print("\n>>> AGGIORNAMENTO COMPLETATO.")
