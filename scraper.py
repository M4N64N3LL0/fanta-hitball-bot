import cloudscraper
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
            nome_clean = re.sub(r'[^A-ZÀÈÉÌÒÙÁÍÓÚ\'\s]', ' ', nome_originale).strip()
            parole = frozenset(nome_clean.split())
            mappa[parole] = {
                'id_documento': nome_originale,
                'nome_display': g['nome_reale'],
                'categoria': g.get('categoria', 'MISTO'),
                'prezzo': g.get('prezzo', 0),
                'squadra': g.get('squadra', '').strip(" :-") 
            }
        return mappa
    except Exception as e: 
        print(f"Errore caricamento JSON: {e}", flush=True)
        return {}

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

def processa_referto(url, tot_casa, tot_trasf, mappa, memoria_punti):
    try:
        res = requests.get(url, headers=HEADERS, timeout=10)
        res.raise_for_status()
        soup = BeautifulSoup(res.text, 'html.parser')
        testo_pagina = soup.get_text(separator=' ')
        
        m_data = re.search(r'(\d{2})-(\d{2})-(\d{4})', testo_pagina)
        if not m_data: return
        data_match = f"{m_data.group(3)}-{m_data.group(2)}-{m_data.group(1)}"

        is_tavolino = "tavolino" in testo_pagina.lower() or (tot_casa == 60 and tot_trasf == 0) or (tot_casa == 0 and tot_trasf == 60)

        if is_tavolino:
            h1 = soup.find('h1')
            if not h1: return
            
            titolo = h1.get_text(strip=True).upper()
            titolo_pulito = re.sub(r'REFERTO\s*PARTITA|REFERTO|PLV\s*HITBALL|:', '', titolo)
            parti = re.split(r'\s+-\s+|\s+VS\s+', titolo_pulito)
            parti = [p.strip() for p in parti if p.strip()]
            
            if len(parti) < 2: return
            
            sq_casa, sq_trasf = parti[-2], parti[-1]
            vincitore = sq_casa if tot_casa > tot_trasf else sq_trasf
            perdente = sq_casa if tot_casa < tot_trasf else sq_trasf

            print(f"      [TAVOLINO] {vincitore} batte {perdente} in data {data_match}", flush=True)

            for parole_json, info_g in mappa.items():
                squadra_g = info_g['squadra'].upper()
                if not squadra_g: continue
                
                voto = None
                if squadra_g in vincitore or vincitore in squadra_g: 
                    voto = 10
                elif squadra_g in perdente or perdente in squadra_g: 
                    voto = -20
                else:
                    parole_sq = [p for p in squadra_g.split() if len(p) > 3]
                    for p in parole_sq:
                        if p in vincitore: 
                            voto = 10; break
                        elif p in perdente: 
                            voto = -20; break
                
                if voto is not None:
                    id_fb = info_g['id_documento']
                    memoria_punti[id_fb][data_match] = voto
            return

        liste_squadre = soup.find_all('ul', class_=re.compile(r'list-group', re.I))
        if len(liste_squadre) < 2: return

        def estrai_e_salva(ul_node, is_casa):
            for li in ul_node.find_all('li', class_=re.compile(r'list-group-item', re.I)):
                testo_originale = li.get_text(separator=' ', strip=True)
                testo_upper = testo_originale.upper().replace('’', "'").replace('‘', "'").replace('`', "'")
                testo_clean = re.sub(r'[^A-ZÀÈÉÌÒÙÁÍÓÚ\'\s]', ' ', testo_upper)
                parole_web = testo_clean.split()

                id_fb = None
                for parole_json, info in mappa.items():
                    if all(p in parole_web for p in parole_json):
                        id_fb = info['id_documento']
                        break
                
                if not id_fb: continue

                punti_tiri = sum(int(q) * int(v) for q, v in re.findall(r'(\d+)\s*x\s*(2|3)', testo_originale, re.I))
                if punti_tiri == 0:
                    m_tot = re.search(r'Tot\.\s*(\d+)', testo_originale, re.I)
                    if m_tot: punti_tiri = int(m_tot.group(1))

                m_auto = re.search(r'(\d+)\s*x\s*AUTOHIT', testo_originale, re.I)
                autohits = int(m_auto.group(1)) if m_auto else 0
                giallo = li.find(class_=re.compile(r'warning|yellow', re.I)) is not None
                rosso = li.find(class_=re.compile(r'danger|red', re.I)) is not None
                
                fatti, subiti = (tot_casa, tot_trasf) if is_casa else (tot_trasf, tot_casa)
                voto = calcola_punteggio_fanta(punti_tiri, autohits, fatti, subiti, giallo, rosso, id_fb in QUOTE_ROSA_FEM, False)
                
                memoria_punti[id_fb][data_match] = voto

        estrai_e_salva(liste_squadre[0], True)
        estrai_e_salva(liste_squadre[1], False)
        
    except requests.exceptions.RequestException as e:
        print(f"      [TIMEOUT/CONNESSIONE] Riproverà al prossimo avvio.", flush=True)
    except Exception as e: 
        print(f"      [ERR] {e}", flush=True)

def recupera_e_analizza(db, mappa):
    memoria_punti = {}
    for info in mappa.values():
        memoria_punti[info['id_documento']] = {}

    for camp_id in ID_CAMPIONATI:
        url_camp = f"https://referto.plvhitball.it/index.php?route=championship/championship/view&championship_id={camp_id}"
        print(f"\n>>> ANALISI CAMPIONATO {camp_id}", flush=True)
        try:
            res = requests.get(url_camp, headers=HEADERS, timeout=15)
            soup = BeautifulSoup(res.text, 'html.parser')
            for a_tag in soup.find_all('a', href=True):
                if 'match_id=' in a_tag['href'] or 'referto_id=' in a_tag['href']:
                    riga = a_tag
                    testo_riga = ""
                    for _ in range(5):
                        if riga.parent:
                            riga = riga.parent
                            testo_riga = riga.get_text()
                            if "Risultato:" in testo_riga: break
                    m_ris = re.search(r'Risultato:\s*(\d+)\s*-\s*(\d+)', testo_riga, re.I)
                    if m_ris:
                        processa_referto("https://referto.plvhitball.it/" + a_tag['href'].lstrip('/'), int(m_ris.group(1)), int(m_ris.group(2)), mappa, memoria_punti)
        except Exception as e: print(f">>> Errore Campionato {camp_id}: {e}", flush=True)

    print("\n>>> INIZIO RISCRITTURA DA ZERO SU FIREBASE...", flush=True)
    giocatori_aggiornati = 0

    for parole_json, info_g in mappa.items():
        id_fb = info_g['id_documento']
        voti_finali = memoria_punti[id_fb]
        
        doc_ref = db.collection('giocatori').document(id_fb)
        
        doc_ref.set({
            'nome_reale': info_g['nome_display'],
            'categoria': info_g['categoria'],
            'prezzo': info_g['prezzo'],
            'squadra': info_g['squadra'],
            'is_fem': id_fb in QUOTE_ROSA_FEM
        }, merge=True)
        
        doc_ref.update({
            'punti_giornate': voti_finali
        })
        giocatori_aggiornati += 1

    print(f">>> OPERAZIONE COMPLETATA! Database azzerato e riscritto in modo pulito per {giocatori_aggiornati} giocatori.", flush=True)

   
