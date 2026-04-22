import os
import json
import requests
import re
from bs4 import BeautifulSoup
import firebase_admin
from firebase_admin import credentials, firestore

# --- CONFIGURAZIONE ---
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
    print(f">>> Cerco il file dei giocatori: {percorso_file}")
    try:
        with open(percorso_file, 'r', encoding='utf-8') as f:
            dati = json.load(f)
        mappa = {}
        for g in dati:
            n_raw = g['nome_reale'].upper()
            n_clean = re.sub(r'[^A-Z\s\']', '', n_raw).strip()
            mappa[n_clean] = {
                'categoria': g['categoria'],
                'prezzo': g['prezzo'],
                'nome_originale': g['nome_reale']
            }
        print(f">>> TROVATI {len(mappa)} GIOCATORI! Avvio la scansione...")
        return mappa
    except Exception as e:
        print(f">>> ERRORE FATALE: Non riesco a caricare i giocatori! Motivo: {e}")
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

def processa_referto(url, tot_casa, tot_trasf, db, mappa):
    try:
        res = requests.get(url, headers=HEADERS, timeout=15)
        soup = BeautifulSoup(res.text, 'html.parser')
        
        # --- NOVITÀ: CERCA LA DATA DENTRO IL REFERTO ---
        data_match = "0000-00-00"
        d_elem = soup.find(string=re.compile(r'\d{2}-\d{2}-\d{4}'))
        if d_elem:
            m_data = re.search(r'(\d{2})-(\d{2})-(\d{4})', d_elem)
            if m_data:
                # La salviamo come YYYY-MM-DD così Firebase la ordina da sola cronologicamente
                data_match = f"{m_data.group(3)}-{m_data.group(2)}-{m_data.group(1)}"
        
        if data_match == "0000-00-00":
            print("      [SKIP] Impossibile trovare la data in questo referto.")
            return

        tavolino = soup.find(string=re.compile(r'vinta a tavolino', re.I)) is not None
        liste_squadre = soup.find_all('ul', class_=re.compile(r'list-group', re.I))
        if len(liste_squadre) < 2: return

        giocatori_match = []
        def estrai(ul_node, is_casa):
            for li in ul_node.find_all('li', class_=re.compile(r'list-group-item', re.I)):
                testo = li.get_text(separator=' ', strip=True)
                if "x" not in testo: continue
                n_raw = re.split(r'\d+\s*x|Tot\.', testo, flags=re.I)[0].strip().upper()
                n_clean = re.sub(r'[^A-Z\s\']', '', n_raw).strip()
                if len(n_clean) < 3: continue
                punti_tiri = sum(int(q) * int(v) for q, v in re.findall(r'(\d+)\s*x\s*(2|3)', testo))
                m_auto = re.search(r'(\d+)\s*x\s*AUTOHIT', testo, re.I)
                autohits = int(m_auto.group(1)) if m_auto else 0
                giallo = li.find(class_=re.compile(r'warning|yellow', re.I)) is not None
                rosso = li.find(class_=re.compile(r'danger|red', re.I)) is not None
                giocatori_match.append({"nome": n_clean, "punti_tiri": punti_tiri, "autohits": autohits, "giallo": giallo, "rosso": rosso, "is_casa": is_casa})

        estrai(liste_squadre[0], True)
        estrai(liste_squadre[1], False)

        for g in giocatori_match:
            if g['nome'] not in mappa: continue
            is_fem = g['nome'] in QUOTE_ROSA_FEM
            fatti = tot_casa if g['is_casa'] else tot_trasf
            subiti = tot_trasf if g['is_casa'] else tot_casa
            tav_match = tavolino and ((g['is_casa'] and tot_casa == 0) or (not g['is_casa'] and tot_trasf == 0))
            voto = calcola_punteggio_fanta(g['punti_tiri'], g['autohits'], fatti, subiti, g['giallo'], g['rosso'], is_fem, tav_match)
            
            # ATTENZIONE: Usa la data come chiave, non più la giornata!
            db.collection('giocatori').document(g['nome']).set({
                'punti_date': { data_match: voto } 
            }, merge=True)
            
            print(f"      [OK] {g['nome']} | {data_match} : {voto}pt")

    except Exception as e:
        print(f"      [ERR] {e}")

def recupera_e_analizza(db, mappa):
    cat_map = {39: "A1", 41: "A2", 42: "B1", 43: "B2"}
    for camp_id in ID_CAMPIONATI:
        cat_label = cat_map.get(camp_id, "")
        url_camp = f"https://referto.plvhitball.it/index.php?route=championship/championship/view&championship_id={camp_id}"
        print(f"\n>>> SCANSIONE {cat_label} (ID: {camp_id})")
        try:
            res = requests.get(url_camp, headers=HEADERS, timeout=15)
            soup = BeautifulSoup(res.text, 'html.parser')
            links = [a for a in soup.find_all('a', href=True) if 'match_id=' in a['href'] or 'referto_id=' in a['href']]
            
            print(f"   -> Trovati {len(links)} referti potenziali.")
            
            for i, a_tag in enumerate(links, 1):
                # Guarda 5 righe sopra il bottone per trovare "Risultato: X-Y"
                riga = a_tag
                for _ in range(5):
                    if riga.parent:
                        riga = riga.parent
                        if "Risultato:" in riga.get_text(): break
                        
                m_ris = re.search(r'Risultato:\s*(\d+)\s*-\s*(\d+)', riga.get_text())
                if m_ris:
                    tot_casa = int(m_ris.group(1))
                    tot_trasf = int(m_ris.group(2))
                    url_match = "https://referto.plvhitball.it/" + a_tag['href'].lstrip('/')
                    print(f"   [{i}/{len(links)}] Entro nel referto (Ris: {tot_casa}-{tot_trasf})...")
                    processa_referto(url_match, tot_casa, tot_trasf, db, mappa)
                    
        except Exception as e:
            print(f">>> Errore durante la scansione della categoria: {e}")

if __name__ == "__main__":
    print(">>> AVVIO BOT FANTAHITBALL (MODALITÀ DATE)...")
    mappa_g = carica_anagrafica_locale()
    if mappa_g:
        db_fs = inizializza_firebase()
        recupera_e_analizza(db_fs, mappa_g)
        print("\n>>> AGGIORNAMENTO COMPLETATO CON SUCCESSO.")
    else:
        print("\n>>> BOT FERMATO: Nessun giocatore trovato.")
